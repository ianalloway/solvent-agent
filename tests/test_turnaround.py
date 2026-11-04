"""Tests for measured paid-to-delivered turnaround (solvent.turnaround)."""

from __future__ import annotations

import time

import pytest

from solvent.turnaround import (
    FLOOR_SECONDS,
    MIN_SAMPLES,
    breaches,
    format_turnaround,
    humanise,
    late_threshold_seconds,
    measure,
    promise_seconds,
    report,
    stats,
)
from solvent.treasury import Treasury



@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    return t



def _delivered(treasury, job_id, *, waited: float, paid_at: float | None = None):
    """A job paid at `paid_at` and delivered `waited` seconds later."""
    start = paid_at if paid_at is not None else time.time() - waited
    treasury.earn(4_900, f"paid {job_id}", job_id=job_id, stripe_ref=f"pi_{job_id}", ts=start)
    treasury.record_event(
        job_id, "delivered", {"stage": "delivered", "job_id": job_id, "ts": start + waited}
    )
    with treasury._conn() as conn, conn:
        conn.execute(
            "UPDATE events SET ts = ? WHERE job_id = ? AND stage = 'delivered'",
            (start + waited, job_id),
        )



def test_nothing_to_measure_yet(treasury):
    assert measure(treasury) == []
    assert stats([])["samples"] == 0
    assert promise_seconds([]) is None
    assert "nothing to measure" in format_turnaround(report(treasury))



def test_the_wait_is_measured_from_payment_to_delivery(treasury):
    _delivered(treasury, "J1", waited=120.0)
    rows = measure(treasury)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "J"
    assert rows[0]["seconds"] == pytest.approx(120.0, abs=0.5)



def test_a_paid_but_undelivered_job_is_not_a_measurement(treasury):
    treasury.earn(4_900, "paid J1", job_id="J1", stripe_ref="pi_1")
    assert measure(treasury) == []



def test_an_unpaid_delivery_is_not_a_measurement(treasury):
    treasury.record_event("J", "delivered", {"stage": "delivered", "job_id": "J"})
    assert measure(treasury) == []



def test_percentiles_describe_the_distribution(treasury):
    for index, waited in enumerate(10.0, 20.0, 30.0, 40.0, 500.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    figures = stats(measure(treasury))

    assert figures["samples"] == 5
    assert figures["p50"] == pytest.approx(30.0, abs=0.5)
    assert figures["max"] == pytest.approx(500.0, abs=0.5)
    assert figures["mean"] == pytest.approx(120.0, abs=1.0)



def test_a_promise_needs_enough_history(treasury):
    for index in range(MIN_SAMPLES - 1):
        _delivered(treasury, f"J{index}", waited=30.0)
    assert promise_seconds(measure(treasury)) is None
    assert "not enough history" in humanise(None)



def test_a_promise_is_never_faster_than_the_floor(treasury):
    for index in range(MIN_SAMPLES + 2):
        _delivered(treasury, f"J{index}", waited=2.0)  # suspiciously quick
    assert promise_seconds(measure(treasury)) == FLOOR_SECONDS




def test_the_promise_sits_at_the_chosen_percentile(treasury):
    for index, waited in enumerate(100.0, 200.0, 300.0, 400.0, 5_000.0]):
        _delivered(treasury, f"J{index}", waited=waited)
    rows = measure(treasury)

    p0 €ôÁÉ½µ¥Í•}Í•½¹‘Ì¡É½İÌ°Á•É•¹Ñ¥±”ôÀ¸Ô¤(€€€ÀäÀ€ôÁÉ½µ¥Í•}Í•½¹‘Ì¡É½İÌ°Á•É•¹Ñ¥±”ôÀ¸ä¤(€€€…ÍÍ•ÉĞÀÂ€¥Ì¹½Ğ9½¹”…¹ÀäÀ¥Ì¹½Ğ9½¹”(€€€…ÍÍ•ÉĞÀÔÀ€ğÀäÀ((()ÁåÑ•ÍĞ¹µ…É­|¹Á…É…µ•ÑÉ¥é” (€€€€‰Í•½¹‘Ì±•áÁ•Ñ•ˆ°(€€€l(€€€€€€€€ ÄÈ¸À°€‰İ¥Ñ¡¥¸€Ôµ¥¹ÕÑ•Ìˆ¤°(€€€€€€€€ ÌÀÀ¸À°€‰İ¥Ñ¡¥¸€Ôµ¥¹ÕÑ•Ìˆ¤°(€€€€€€€€ ÌÀÄ¸À°€‰İ¥Ñ¡¥¸€ÄÀµ¥¹ÕÑ•Ìˆ¤°(€€€€€€€€ Å|ÔÀÀ¸À°€‰İ¥Ñ¡¥¸€ÌÀµ¥¹ÕÑ•Ìˆ¤°(€€€€€€€€ İ|ÀÀÀ¸À°€‰İ¥Ñ¡¥¸€È¡½ÕÈ¡Ì¤ˆ¤°(€€€€€€€€ ÈÀÁ|ÀÀÀ¸À°€‰İ¥Ñ¡¥¸€Ì‘…ä¡Ì¤ˆ¤°(€€€t°(¤)‘•˜Ñ•ÍÑ}…}ÁÉ½µ¥Í•}É½Õ¹‘Í}ÕÁ}Ñ½}Í½µ•Ñ¡¥¹}ÅÕ½Ñ…‰±”¡Í•½¹‘Ì°•áÁ•Ñ•¤è(€€€…ÍÍ•ÉĞ¡Õµ…¹¥Í”¡Í•½¹‘Ì¤€ôô•áÁ•Ñ•((()‘•˜Ñ•ÍÑ}‰É•…¡•Í}…É•}Ñ¡•}©½‰Í}Ñ¡…Ñ}Ñ½½­}±½¹•È¡ÑÉ•…ÍÕÉä¤è(€€€™½È¥¹‘•à°İ…¥Ñ•¥¸•¹Õµ•É…Ñ” lØÀ¸À°€ØÀ¸À°€ØÀ¸À°€å|ÀÀÀ¸Át¤è(€€€€€€€}‘•±¥Ù•É•¡ÑÉ•…ÍÕÉä°˜‰)í¥¹‘•áôˆ°İ…¥Ñ•õİ…¥Ñ•¤(€€€É½İÌ€ôµ•…ÍÕÉ”¡ÑÉ•…ÍÕÉä¤(€€€ÁÉ½µ¥Í”€ôÁÉ½µ¥Í•}Í•½¹‘Ì¡É½İÌ°Á•É•¹Ñ¥±”ôÀ¸Ô¤((€€€µ¥ÍÍ•€ô‰É•…¡•Ì¡É½İÌ°ÁÉ½µ¥Í”¤(€€€…ÍÍ•ÉĞmÉ½Ü‰©½‰}¥‰t™½ÈÉ½Ü¥¸µ¥ÍÍ•‘t€ôôl‰(Ì‰t(€€€…ÍÍ•ÉĞ‰É•…¡•Ì¡É½İÌ°9½¹”¤€ôômt((()‘•˜Ñ•ÍÑ}±…Ñ•}¥Í}‘•™¥¹•‘}‰å}Ñ¡•}Í¡½Á}Í}½İ¹}¡¥ÍÑ½Éä¡ÑÉ•…ÍÕÉä¤è(€€€™½È¥¹‘•à¥¸É…¹”¡5%9}M5A1Lˆ¬€Ä¤è(€€€€€€€}‘•±¥Ù•É•¡ÑÉ•…ÍÕÉä°˜‰)í¥¹‘•áôˆ°İ…¥Ñ•ôÄÈÀ¸À¤(€€€Ñ¡É•Í¡½±€ô±…Ñ•}Ñ¡É•Í¡½±‘}Í•½¹‘Ì¡ÑÉ•…ÍÕÉä¤(€€€…ÍÍ•ÉĞÑ¡É•Í¡½±¥Ì¹½Ğ9½¹”(€€€…ÍÍ•ÉĞÑ¡É•Í¡½±€ø€ÄÈÀ¸À€ŒÍ±…¬…‰½Ù”Ñ¡”¹½Éµ…°İ…¥Ğ(((()‘•˜Ñ•ÍÑ}İ¥Ñ¡½ÕÑ}¡¥ÍÑ½Éå}Ñ¡•É•}¥Í}¹½}µ•…ÍÕÉ•‘}Ñ¡É•Í¡½±¡ÑÉ•…ÍÕÉä¤è(€€€…ÍÍ•ÉĞ±…Ñ•}Ñ¡É•Í¡½±‘}Í•½¹‘Ì¡ÑÉ•…ÍÕÉä¤¥Ì9½¹”(((()‘•˜Ñ•ÍÑ}Ñ¡•}É•Á½ÉÑ}É•¹‘•ÉÌ¡ÑÉ•…ÍÕÉä¤è(€€€™½È¥¹‘•à°İ…¥Ñ•¥¸•¹Õµ•É…Ñ”¡lÌÀ¸À°€ĞÔ¸À°€ØÀ¸À°€Ñ|ÀÀÀ¸Át¤è(€€€€€€€}‘•±¥Ù•É•¡ÑÉ•…ÍÕÉä°˜‰)í¥¹‘•áôˆ°İ…¥Ñ•õİ…¥Ñ•¤(€€€‘…Ñ„€ôÉ•Á½ÉĞ¡ÑÉ•…ÍÕÉä¤(€€€É•¹‘•É•€ô™½Éµ…Ñ}ÑÕÉ¹…É½Õ¹¡‘…Ñ„¤((€€€…ÍÍ•ÉĞ€‰QUI9I=U9ˆ¥¸É•¹‘•É•(€€€…ÍÍ•ÉĞ€‰EÕ½Ñ…‰±”ÁÉ½µ¥Í”ˆ¥¸É•¹‘•É•(€€€…ÍÍ•ÉĞ€‰(Ìˆ¥¸É•¹‘•É•€€ŒÑ¡”Í±½İ•ÍĞ©½ˆ¥Ì¹…µ•((((Œ€´´´Ñ¡”…±•ÉĞ…¹Ñ¡”ÅÕ½Ñ”ÕÍ”¥Ğ€´´´´´´´´´´´´´´´´´´´´´´´´´´´´´(()‘•˜Ñ•ÍÑ}…±•ÉÑÍ}™…±±}‰…­}Ñ½}™¥á•‘}¡½ÕÉÍ}İ¥Ñ¡½ÕÑ}¡¥ÍÑ½Éä¡ÑÉ•…ÍÕÉä¤è(€€€™É½´Í½±Ù•¹Ğ¹…±•ÉÑÌ¥µÁ½ÉĞ]I8°¡•­}ÍÑÕ­}©½‰Ì((€€€ÑÉ•…ÍÕÉä¹ÕÁÍ•ÉÑ}©½ˆ ‰(Äˆ°€‰Á…¥‘}Á•¹‘¥¹}™Õ±™¥±°ˆ°Ñ½Á¥Œô‰Ğˆ°‰Õ‘•Ñ}•¹ÑÌôÑ|äÀÀ¤(€€€…ÍÍ•ÉĞ¡•­}ÍÑÕ­}©½‰Ì¡ÑÉ•…ÍÕÉä°¹½ÜõÑ¥µ”¹Ñ¥µ” ¤€¬€Ì€¨€Í|ØÀÀ¤¹Í•Ù•É¥Ñä€ôô]I8((()‘•˜Ñ•ÍÑ}…±•ÉÑÍ}ÕÍ•}Ñ¡•}µ•…ÍÕÉ•‘}Ñ¡É•Í¡½±‘}İ¡•¹}Ñ¡•É•}¥Í}¡¥ÍÑ½Éä¡ÑÉ•…ÍÕÉä¤è(€€€€ˆˆ‰Í¡½ÀÑ¡…Ğ‘•±¥Ù•ÉÌ¥¸Ñİ¼µ¥¹ÕÑ•ÌÍ¡½Õ±¹½Ğİ…¥ĞÑİ¼¡½ÕÉÌÑ¼¹½Ñ¥”¸ˆˆˆ(€€€™É½´Í½±Ù•¹Ğ¹…±•ÉÑÌ¥µÁ½ÉĞ=,°]I8°¡•­}ÍÑÕ­}©½‰Ì((€€€™½È¥¹‘•à¥¸É…¹”¡5%9}M5A1L€¬€Ä¤è(€€€€€€€}‘•±¥Ù•É•¡ÑÉ•…ÍÕÉä°˜‰í¥¹‘•áôˆ°İ…¥Ñ•ôÄÈÀ¸À¤(€€€ÑÉ•…ÍÕÉä¹ÕÁÍ•ÉÑ}©½ˆ ‰(Äˆ°€‰Á…¥‘}Á•¹‘¥¹}™Õ±™¥±°ˆ°Ñ½Á¥Œô‰Ğˆ°‰Õ‘•Ñ}•¹ÑÌôÑ|äÀÀ¤((€€€€Œ]•±°¥¹Í¥‘”Ñ¡”µ•…ÍÕÉ•Ñ¡É•Í¡½±è¹½Ñ¡¥¹œÑ¼Í…äå•Ğ¸(€€€…ÍÍ•ÉĞ¡•­}ÍÑÕ­}©½‰Ì¡ÑÉ•…ÍÕÉä°¹½ÜõÑ¥µ”¹Ñ¥µ” ¤€¬€ØÀ¤¹Í•Ù•É¥Ñä€ôô=,(€€€€Œ¸¡½ÕÈ¥¸°™…È‰•å½¹„Ñİ¼µµ¥¹ÕÑ”Í¡½ÀÌÀäÔ°‰ÕĞÕ¹‘•ÈÑ¡”½±€É rule.
    assert check_stuck_jobs(treasury, now=time.time() + 3_600).severity == WARN



def test_the_quote_quotes_the_measured_turnaround(treasury, monkeypatch, capsys):
    import sys

    from solvent import quote_cmd

    rows = [
        {"job_id": f"J{i}", "paid_at": 0.0, "delivered_at": 90.0, "seconds": 90.0} for i in range(4)
    ]
    monkeypatch.setattr("solvent.turnaround.measure", lambda *a, **k: rows)
    monkeypatch.setttr(sys, "argv", ["solvent quote", "A opic", "--budget", "49"])

    with pytest.raises(SystemExit) as exit_info:
        quote_cmd.main()
    assert exit_info.value.code == 0
    assert "Turnaround          within 5 minutes" in capsys.readouterr().out



# Direct unit tests for pure helpers (issue #132)

@prtest.mark.parametrize(
    "values, fraction, expected",
    [
        ([], 0.5, 0.0),
        ([42.0], 0.0, 42.0),
        ([42.0], 0.5, 42.0),
        ([42.0], 1.0, 42.0),
        ([10.0, 20.0, 30.0], 0.0, 10.0),
        ([10.0, 20.0, 30.0], 0.5, 20.0),
        ([10.0, 20.0, 30.0], 0.9, 30.0),
        ([10.0, 20.0, 30.0], 1.0, 30.0),
        # unsorted input
        ([30.0, 10.0, 20.0], 0.5, 20.0),
        ([5.0, 1.0, 10.0, 3.0], 0.25, 3.0),
    ],
)
def test_percentile(values, fraction, expected):
    from solvent.turnaround import _percentile
    assert _percentile(values, fraction) == expected




@pytest.mark_.parametrize(
    "seconds, expected",
    [
        (None, "\u2014"),
        (89.9, "89.9s"),
        (90, "1.5m"),
        (5399, "90.0m"),
        (5400, "1.5h"),
        (7200, "2.0h"),
        (0, "0.0s"),
        (60, "60.0s"),
    ],
)
def test_duration(seconds, expected):
    from solvent.turnaround import _duration
    assert _duration(seconds) == expected
