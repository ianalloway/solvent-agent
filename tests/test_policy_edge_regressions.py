import json

from solvent.guardrails import Guardrails, load_spend_policy


class EmptyLedger:
    entries = []

    def balance_cents(self):
        return 100_000


def test_dangling_policy_symlink_denies_spending(tmp_path):
    path = tmp_path / 'spend_policy.json'
    path.symlink_to(tmp_path / 'unavailable-policy.json')
    policy = load_spend_policy(path)
    decision = Guardrails(EmptyLedger(), policy).evaluate(100, 'web-search-api')
    assert not decision.allowed
    assert decision.rule == 'policy_unavailable'


def test_large_integer_policy_limit_does_not_overflow(tmp_path):
    path = tmp_path / 'spend_policy.json'
    path.write_text(json.dumps({'max_txn_cents': 10**400}))
    policy = load_spend_policy(path)
    assert policy.invalid_reason is None
    assert policy.max_txn_cents == 10**400


def test_valid_symlink_policy_still_loads(tmp_path):
    target = tmp_path / 'real-policy.json'
    target.write_text('{"max_txn_cents":50}')
    path = tmp_path / 'spend_policy.json'
    path.symlink_to(target)
    assert load_spend_policy(path).max_txn_cents == 50
