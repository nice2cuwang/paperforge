"""Tests for S2: evidence strength from research design, not word count.

Before, ``infer_strength`` ranked by word count (>=180 => high, >=80 => medium),
so a long methods paragraph outranked a concise statistical result like
"p<0.001, n=1000". Now design keywords + statistical signals decide.
"""
from __future__ import annotations

from app.services.evidence_service import infer_strength


def test_high_design_keywords():
    assert infer_strength("We performed a meta-analysis of 14 trials.") == "high"
    assert infer_strength("A randomized controlled trial compared both arms.") == "high"
    assert infer_strength("系统综述纳入了 32 项随机对照研究。") == "high"


def test_reported_statistics_are_high():
    # The plan's motivating example: concise stats must NOT be low.
    assert infer_strength("The effect was significant (p<0.001, n=1000).") == "high"
    assert infer_strength("HR = 0.62 (95% confidence interval 0.48-0.81).") == "high"


def test_empirical_and_quantitative_are_medium():
    assert infer_strength("Empirical results from the benchmark suite.") == "medium"
    assert infer_strength("性能提升 23%，显著优于基线。") == "medium"


def test_long_vague_prose_is_low_not_high():
    long_vague = (
        "This paper discusses various aspects of the field in detail. "
        "We examine multiple approaches and consider their strengths and weaknesses. "
        "The literature contains a wide range of perspectives on the topic. "
        "In general, the field has evolved considerably over the years. "
        "Many researchers have contributed valuable work in this area. "
        "The overall picture remains complex and multifaceted. "
        "Different authors emphasize different aspects of the problem. "
        "A comprehensive understanding requires careful consideration. "
        "Future work will likely build on these foundations. "
        "The implications of these developments are substantial. "
    ) * 3  # > 180 words, but no design or statistical signal
    assert infer_strength(long_vague) == "low"


def test_hedged_claims_are_low():
    assert infer_strength("The findings may indicate a possible link.") == "low"
