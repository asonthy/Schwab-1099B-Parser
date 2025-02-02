#!/usr/bin/env python
"""Convert Schwab EAC 1099 PDF to TurboTax TXF format. No need to copy-paste.

Usage:
    python3 taxform_parser.py GOOG.pdf GOOG.txf
    python3 taxform_parser.py GOOGL.pdf GOOGL.txf
"""

# TXF spec: http://turbotax.intuit.com/txf/TXF042.jsp

from __future__ import absolute_import, division, print_function

import logging
import subprocess
import sys
from datetime import date, datetime
from decimal import *
from io import BytesIO
import zipfile

SCHWAB_DATE_FORMAT = "%m/%d/%Y"


class Record:
    quantity = 0
    symbol = 'UNKNOWN'
    acquisition_date = date.fromtimestamp(0)
    sale_date = date.fromtimestamp(0)
    fmv = None
    cusip = None

    # these fields are reported by schwab
    total_reported_basis = None
    total_reported_wash = None
    total_reported_proceeds = None

    def __str__(self):
        return "symbol: %s (%s),\tacq_date: %s,\tsale_date: %s,\tquantity: %2f,\tproceeds: $%9.2f,\treported_basis: $%9.2f,\treported_wash: $ %6.2f" % (
            self.symbol, self.cusip, self.acquisition_date.strftime(SCHWAB_DATE_FORMAT),
            self.sale_date.strftime(SCHWAB_DATE_FORMAT), self.quantity, float(self.total_reported_proceeds),
            float(self.total_reported_basis),
            float(self.total_reported_wash) if self.total_reported_wash else 0)

    def __repr__(self):
        return self.__str__()

    def compute_total_basis(self):
        return self.fmv * self.quantity if self.fmv else self.total_reported_basis

    def compute_total_wash(self):
        return "%.2f" % float(self.total_reported_wash) if self.total_reported_wash else ''

    @staticmethod
    def txf_records(records):
        # A TXF record here looks like:
        # TD            Detailed Record
        # N715          Refnumber
        # C1            Copy number
        # L1            Line number
        # P50 QCOM      Description
        # D01/02/2010   Date acquired
        # D01/15/2011   Date sold
        # $1500         Cost Basis
        # $1300         Sales Net
        # $200          Disallowed wash sale amount
        # ^
        result = "V042\nA quick and dirty TXF script\nD%s\n^\n" % date.today().strftime('%m/%d/%Y')
        for r in records:
            result += "TD\nN715\nC1\nL1\nP%s %s\nD%s\nD%s\n$%.2f\n$%.2f\n$%s\n^\n" % (
                r.quantity, r.symbol, r.acquisition_date.strftime(SCHWAB_DATE_FORMAT),
                r.sale_date.strftime(SCHWAB_DATE_FORMAT),
                r.compute_total_basis(), float(r.total_reported_proceeds), r.compute_total_wash())

        return result


class ParseException(ValueError):
    """Exception raised for errors in the input.

    Attributes:
        expr -- input expression in which the error occurred
        msg  -- explanation of the error
    """

    pass


class Form1099Tokenizer:

    def __init__(self, filename):
        self.filename = filename
        self.lines = [l.strip() for l in
                      subprocess.check_output(['pdftotext', '-raw', filename, '-'], universal_newlines=True).split('\n')]
        self.line_idx = 0
        self.tokens = []

    def find_cusip(self):
        while self.line_idx < len(self.lines):
            cusip = self.lines[self.line_idx]
            self.line_idx += 1
            if cusip.startswith('3825') or cusip.startswith('0207'):
                return cusip
        return None

    def next_token(self):
        if not self.tokens:
            if self.line_idx >= len(self.lines):
                return None
            self.tokens = self.lines[self.line_idx].split(' ')
            self.line_idx += 1
        return self.tokens.pop(0)

    def cur_string_tokens_available(self):
        return len(self.tokens) > 0

    def generate_parsing_error(self):
        return ParseException('ERROR: Parsing input line %d: %s' % (self.line_idx, self.lines[self.line_idx - 1]))



def parse1099Form(filename):
    tokenizer = Form1099Tokenizer(filename)

    records = []
    total_proceeds = Decimal('0.00')
    total_basis = Decimal('0.00')
    total_wash = Decimal('0.00')
    total_num_records = 0

    while True:
        # Typically each record has following structure:
        #     <CUSIP Number>
        #     <Num> SHARES OF GOOG/GOOGL
        #     <Acquisition Date> <Net proceeds> <Cost or other basis> <wash sale disallowed>? <non-covered security>
        #     <Sale Date> Gross <Wash sale disallowed amount>?
        #
        # CUSID: 02079K305 or 38259P508 for GOOGL; 02079K107 or 38259P706 for GOOG
        #
        # Standard example:
        #
        #     02079K107
        #     2 SHARES OF GOOG
        #     01/27/2020 2,933.94 2,933.42 X
        #     02/06/2020 GROSS
        #
        # However variations are possible:
        #
        #     02079K305
        #     1.0 SHARES OF GOOGL
        #     11/25/2015 730.98 769.63 X
        #     05/24/2016
        #     <Wash sale amount>
        #     GROSS
        #
        #     02079K107
        #     16.5432 SHARES OF GOOG 08/25/2020 27,212.97 26,273.91 X
        #     08/28/2020 GROSS
        #

        cusip = tokenizer.find_cusip()
        if not cusip:
            break

        num_shares = tokenizer.next_token()
        if tokenizer.next_token() != 'SHARES' or tokenizer.next_token() != 'OF':
            raise tokenizer.generate_parsing_error()
        try:
            num_shares = float(num_shares)
        except ValueError:
            raise tokenizer.generate_parsing_error()

        symbol = tokenizer.next_token()
        if (symbol != 'GOOG' and symbol != 'GOOGL') or num_shares <= 0:
            raise tokenizer.generate_parsing_error()

        acq_date = tokenizer.next_token()
        proceeds = tokenizer.next_token()
        basis = tokenizer.next_token()
        wash = tokenizer.next_token()
        if wash == 'X':
            wash = ''
            non_covered = 'X'
        else:
            non_covered = tokenizer.next_token()

        if non_covered != 'X':
            raise tokenizer.generate_parsing_error()

        sale_date = tokenizer.next_token()
        maybe_wash_sale_amount = tokenizer.next_token()
        if maybe_wash_sale_amount != 'GROSS':
            wash = maybe_wash_sale_amount
            if tokenizer.next_token() != 'GROSS':
                raise tokenizer.generate_parsing_error()

        if tokenizer.cur_string_tokens_available():
            raise tokenizer.generate_parsing_error()

        record = Record()
        record.quantity = num_shares
        record.cusip = cusip
        record.symbol = symbol
        record.sale_date = datetime.strptime(sale_date, SCHWAB_DATE_FORMAT)
        record.total_reported_proceeds = Decimal(proceeds.replace(',', ''))
        record.total_reported_basis = Decimal(basis.replace(',', ''))
        record.total_reported_wash = Decimal(wash.replace(',', '')) if wash else None
        record.acquisition_date = datetime.strptime(acq_date, SCHWAB_DATE_FORMAT)

        logging.info("Read record: (%s)" % record.__str__())

        total_num_records += 1
        total_proceeds += record.total_reported_proceeds
        total_basis += record.total_reported_basis
        total_wash += record.total_reported_wash if record.total_reported_wash else Decimal(0)

        records.append(record)

    logging.info("File \"%s\" has %d records:\n Total Proceeds: $%.2f, Total Basis: $%.2f, Total Wash: $%.2f"
                 % (filename, total_num_records, total_proceeds, total_basis, total_wash))

    return records


if __name__ == '__main__':
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    ch.setFormatter(formatter)
    root.addHandler(ch)

    records = []
    pdfname = sys.argv[1]
    outfilename = sys.argv[2]
    print('Converting from %s to %s' % (pdfname, outfilename))
    records = parse1099Form(pdfname)
    records.sort(key=lambda x: x.sale_date)
    if len(records) == 0:
        raise Exception('No records found in %s' % pdfname)
    if outfilename.endswith('.txf'):
        with open(outfilename, 'w') as outfile:
            outfile.write(Record.txf_records(records))
    else:
        raise Exception('Unexpected output file format: %s' % outfilename)
    print('Converted %s to %s' % (pdfname, outfilename))