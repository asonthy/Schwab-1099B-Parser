#!/usr/bin/python3
"""Convert copy/paste Schwab EAC 1099-B PDF to TurboTax TXF format.

Based on Xoogler jbeda@'s original script:
http://g/financial-planning/0dCT7yoZGRQ/AOv7F3YklJgJ

Using pdftotext thanks to mdbrown@'s MSSB script:
https://github.com/m-d-brown/mssb-1099b-txf/blob/main/mssb_1099b_to_txf.py
"""

import collections
import dataclasses
import datetime
import decimal
import re
import subprocess
import sys

_CUSIPS = {
    'GOOGL': '02079K305',  # Class A
    'GOOG': '02079K107',  # Class C
}

_TXF_CATEGORIES = {
    'B': 711,  # Short-term noncovered
    'E': 713,  # Long-term noncovered
}


class TxfRecord:

  def __init__(self):
    self._fields = []

  def addField(self, op, value):
    self._fields.append((op, value))

  def __str__(self):
    return '\n'.join(['%s%s' % field for field in self._fields] + ['^'])


@dataclasses.dataclass
class Totals:
  proceeds: decimal.Decimal = decimal.Decimal('0.00')
  basis: decimal.Decimal = decimal.Decimal('0.00')
  wash: decimal.Decimal = decimal.Decimal('0.00')

totals = collections.defaultdict(Totals)


len(sys.argv) == 3 or sys.exit(
    'Usage: %s <input.pdf> <output.txf>' % sys.argv[0])
input_filename = sys.argv[1]
output_filename = sys.argv[2]

lines = subprocess.check_output(['pdftotext', '-raw', input_filename,
                                 '-']).decode('utf-8').splitlines()

with open(output_filename, 'w') as output:

  # Write out the TXF header.
  header = TxfRecord()
  header.addField('V', '042')
  header.addField('A', 'Self')
  header.addField('D', datetime.date.today().strftime('%m/%d/%Y'))
  print(header, file=output)

  input_line = 0
  txf_category = None
  while input_line < len(lines):
    line = lines[input_line]

    m = re.search(r'Box ([%s]) checked' % ''.join(_TXF_CATEGORIES.keys()), line)
    if m:
      txf_category = m.group(1)
      input_line += 1
      continue

    if line not in _CUSIPS.values():
      input_line += 1
      continue

    if txf_category is None:
      sys.exit('Error: could not determine applicable Form 8949 checkbox')

    # Read 4 lines from the input file. It will look something like this:
    #
    #   02079K107
    #   3 SHARES OF GOOG
    #   07/25/2019 4,498.54 3,413.43 1,100.22 X
    #   08/06/2020 GROSS
    #
    # These fields are:
    #
    #   <CUSIP Number>
    #   <Box 1a: Description of Property>
    #   <Box 1b: Date Acquired MM/DD/YYYY> <Box 1d: Proceeds>
    #       <Box 1e: Cost or Other Basis> [Box 1g: Wash Sale Loss Disallowed]
    #       [Box 5: Noncovered Security 'X']
    #   <Box 1c: Date Sold or Disposed MM/DD/YYYY>
    #       <Box 6: Reported to IRS 'GROSS'>
    if len(lines) < input_line + 3:
      sys.exit('Error: insufficient content at end of file: \n%s' %
               repr(lines[input_line:]))

    # Line 1
    cusip = line.upper()

    input_line += 1
    line = lines[input_line]

    # Sometimes next 2 lines are joined. Check for that first.
    m = re.match(
        (
            r'\d+(?:\.\d+)? SHARES? OF (GOOGL?)'
            r' (\d{2}/\d{2}/\d{4})\s+([\d,]+(?:\.\d+))\s+([\d,]+(?:\.\d+))'
            r'(?:\s+([\d,]+(?:\.\d+)))?\s+X$'
        ),
        line,
    )
    if m:
      symbol = m.group(1)
      if _CUSIPS[symbol] != cusip:
        sys.exit(
            'Error unexpected (symbol, CUSIP) pair: (%s, %s)' % (symbol, cusip)
        )
      box1a_description = line

      acq_date = m.group(2)
      proceeds = m.group(3)
      basis = m.group(4)
      wash = m.group(5)
    else:
      # Line 2
      m = re.match(r'\d+(?:\.\d+)? SHARES? OF (GOOGL?)$', line)
      if not m:
        sys.exit('Error parsing input line %d: %s' % (input_line + 1, line))
      symbol = m.group(1)
      if _CUSIPS[symbol] != cusip:
        sys.exit(
            'Error unexpected (symbol, CUSIP) pair: (%s, %s)' % (symbol, cusip)
        )
      box1a_description = line

      input_line += 1
      line = lines[input_line]

      # Line 3
      m = re.match(
          (
              r'(\d{2}/\d{2}/\d{4})\s+([\d,]+(?:\.\d+))\s+'
              '([\d,]+(?:\.\d+))(?:\s+([\d,]+(?:\.\d+)))?\s+X$'
          ),
          line,
      )
      if not m:
        sys.exit('Error parsing input line %d: %s' % (input_line + 1, line))
      acq_date = m.group(1)
      proceeds = m.group(2)
      basis = m.group(3)
      wash = m.group(4)

    input_line += 1
    line = lines[input_line]

    # Line 4 (or line 3)
    m = re.match('(\d{2}/\d{2}/\d{4})\sGROSS$', line)
    if not m:
      sys.exit('Error parsing input line %d: %s' % (input_line + 1, line))
    sale_date = m.group(1)

    input_line += 1

    totals[txf_category].proceeds += decimal.Decimal(proceeds.replace(',', ''))
    totals[txf_category].basis += decimal.Decimal(basis.replace(',', ''))
    if wash is not None:
      totals[txf_category].wash += decimal.Decimal(wash.replace(',', ''))

    # A TXF record here looks like:
    #   TD                  # Detailed Record
    #   N715                # Refnumber (713: Box E, TODO rest)
    #   P50 shares of GOOG  # Description of Property
    #   D01/02/2010         # Date Acquired MM/DD/YYYY
    #   D01/15/2011         # Date Sold or Disposed MM/DD/YYYY
    #   $1,500.10           # Cost or Other Basis
    #   $1,300.31           # Proceeds
    #   $200.01             # [optional: Wash Sale Loss Disallowed]
    #   ^                   # End of record
    item = TxfRecord()
    item.addField('T', 'D')
    item.addField('N', _TXF_CATEGORIES[txf_category])
    item.addField('P', box1a_description)
    item.addField('D', acq_date)
    item.addField('D', sale_date)
    item.addField('$', basis)
    item.addField('$', proceeds)
    if wash is not None:
      item.addField('$', wash)
    print(item, file=output)

print('TXF file written to %s.' % output_filename)
print()
print('Verify these totals match those at the end of your Schwab Statement:')
# All dollar values are both initialized with two digits of precision and
# the values reported by Schwab also have two digits of precision, Decimal
# will automatically output with two digits of precision.
for category, total in totals.items():
  print(f'  Category: Box {category} Checked')
  print(f'    Total Proceeds (Box 1d):                     ${total.proceeds}')
  print(f'    Total Cost or Other Basis (Box 1e):          ${total.basis}')
  print(f'    Total Wash Sale Losses Disallowed (Box 1g):  ${total.wash}')