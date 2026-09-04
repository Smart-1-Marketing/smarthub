from modules.check_reconciliation import app as cr


def test_normalize_name_legal_suffixes():
    assert cr._normalize_name('Trasin Corporation') == 'trasin'
    assert cr._normalize_name('N2 Advertising, LLC') == 'n2 advertising'


def test_name_match_prefers_shared_distinctive_name():
    a = {'DisplayName': 'Trasin Asphalt and Concrete'}
    b = {'DisplayName': 'Unrelated Company'}
    assert cr._score_name('Trasin Corporation', a) > cr._score_name('Trasin Corporation', b)


def test_suggest_exact_invoice():
    invoices = [{'id': '1', 'balance': 24.50, 'late_fees': 0}]
    result = cr._suggest_allocations(24.50, invoices)
    assert result['allocations'] == [{'invoice_id': '1', 'amount': 24.50}]


def test_suggest_pre_late_fee_invoice():
    invoices = [{'id': '1', 'balance': 25.24, 'late_fees': .74}]
    result = cr._suggest_allocations(24.50, invoices)
    assert result['late_fee_warning'] is True
    assert result['allocations'][0]['invoice_id'] == '1'


def test_suggest_multi_invoice_combination():
    invoices = [
        {'id': 'a', 'balance': 147.00, 'late_fees': 0},
        {'id': 'b', 'balance': 147.00, 'late_fees': 0},
        {'id': 'c', 'balance': 169.00, 'late_fees': 0},
        {'id': 'd', 'balance': 169.00, 'late_fees': 0},
    ]
    result = cr._suggest_allocations(632.00, invoices)
    assert len(result['allocations']) == 4
    assert round(sum(x['amount'] for x in result['allocations']), 2) == 632.00


def test_payment_payload_links_invoice():
    payload = cr._payment_payload('3176', '2026-08-11', '1234',
                                  [{'invoice_id': '99', 'amount': 2000.0}],
                                  'Cars & Carts Automotive')
    assert payload['CustomerRef']['value'] == '3176'
    assert payload['TotalAmt'] == 2000.0
    assert payload['PaymentRefNum'] == '1234'
    assert payload['Line'][0]['LinkedTxn'][0] == {'TxnId': '99', 'TxnType': 'Invoice'}
