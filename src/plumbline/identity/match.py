"""Probabilistic matching with Splink (Fellegi-Sunter), clustered into persons.

Why probabilistic and not rules: a shared household email, a typo'd last name and a stale address
each break a different hand-written rule. Fellegi-Sunter learns, from the data itself and without
labels, how much each kind of agreement or disagreement says about "same person", and adds the
evidence up. The answer key is never used for training; it is only used to score the result.
"""
from __future__ import annotations

import logging

import pandas as pd
import splink.comparison_level_library as cll
import splink.comparison_library as cl
from splink import DuckDBAPI, Linker, SettingsCreator, block_on

logging.getLogger("splink").setLevel(logging.ERROR)

MATCH_THRESHOLD = 0.9   # fixed before scoring; the evaluation reports how results move around it

# What counts as a mistyped email. The first version used "within 2 edits", and precision fell as the
# world grew: 97.4% at 100,000 people, 93.5% at 300,000. Of the pairs it wrongly joined, 89% were
# linked by that level, against 3% of right pairs. Addresses here are built from names, so two
# namesakes differ by a separator or a digit (scott.williams / scott_williams, ryan.green36 /
# ryan.green32), and Fellegi-Sunter, which treats fields as independent, counted the name twice.
# A typo changes a letter: one edit, same provider, and the letters themselves differ.
EMAIL_TYPO_SQL = ("levenshtein(email_l, email_r) <= 1 AND split_part(email_l, '@', 2) = split_part(email_r, '@', 2) "
                  "AND regexp_replace(email_l, '[^a-z]', '', 'g') <> regexp_replace(email_r, '[^a-z]', '', 'g')")

BLOCKING = [
    block_on("email"),
    block_on("phone"),
    block_on("last_name", "zip"),
    block_on("first_group", "zip"),
    block_on("house", "street", "zip"),
    block_on("first_group", "last_name"),
]


def settings() -> SettingsCreator:
    first = cl.CustomComparison(output_column_name="first_name", comparison_levels=[
        cll.NullLevel("first_name"),
        cll.ExactMatchLevel("first_name").configure(tf_adjustment_column="first_name"),
        cll.ExactMatchLevel("first_group").configure(label_for_charts="Same nickname group"),
        cll.JaroWinklerLevel("first_name", 0.88),
        cll.ElseLevel()])
    address = cl.CustomComparison(output_column_name="address", comparison_levels=[
        cll.Or(cll.NullLevel("street"), cll.NullLevel("house")),
        cll.And(cll.ExactMatchLevel("house"), cll.ExactMatchLevel("street"), cll.ExactMatchLevel("unit")),
        cll.And(cll.ExactMatchLevel("house"), cll.ExactMatchLevel("street")),
        cll.And(cll.ExactMatchLevel("house"), cll.LevenshteinLevel("street", 2)),
        cll.ElseLevel()])
    # Only vendor rows carry a date of birth, so almost no training pair has one on both sides and
    # EM cannot learn these weights. They are set by hand instead and marked fixed:
    # a true match nearly always agrees on DOB (m), two random people rarely do (u is still learned).
    dob = cl.CustomComparison(output_column_name="dob", comparison_levels=[
        cll.NullLevel("birth_year"),
        cll.ExactMatchLevel("dob").configure(m_probability=0.93, fix_m_probability=True),
        cll.ExactMatchLevel("birth_year").configure(m_probability=0.05, fix_m_probability=True),
        cll.ElseLevel().configure(m_probability=0.02, fix_m_probability=True)])
    return SettingsCreator(
        link_type="dedupe_only",
        unique_id_column_name="unique_id",
        comparisons=[
            first,
            cl.NameComparison("last_name").configure(term_frequency_adjustments=True),
            # No "same username" level: usernames are built from names (brandonanderson@...), so
            # rewarding them counts the name twice. Two people with one name at two providers
            # would look like one person. The typo level is narrow for the same reason (EMAIL_TYPO_SQL).
            cl.CustomComparison(output_column_name="email", comparison_levels=[
                cll.NullLevel("email"),
                cll.ExactMatchLevel("email"),
                cll.CustomLevel(EMAIL_TYPO_SQL, label_for_charts="One-letter typo, same provider"),
                cll.ElseLevel()]),
            cl.ExactMatch("phone"),
            address,
            cl.ExactMatch("zip"),
            dob,
        ],
        blocking_rules_to_generate_predictions=BLOCKING,
        retain_intermediate_calculation_columns=False,
        retain_matching_columns=False,
    )


def train_and_predict(records: pd.DataFrame, seed: int = 7, threshold: float = MATCH_THRESHOLD):
    """-> (pairs with match_probability, clusters unique_id -> cluster_id, linker)."""
    linker = Linker(records, settings(), db_api=DuckDBAPI(), set_up_basic_logging=False)
    t = linker.training
    t.estimate_probability_two_random_records_match(
        [block_on("email", "last_name"), block_on("phone", "last_name")], recall=0.7)
    t.estimate_u_using_random_sampling(max_pairs=5e6, seed=seed)
    # Training blocks are chosen so household members cannot dominate them. Family members share a
    # last name, address and zip (and 2,700 people share a household email), so a block on those is
    # mostly families, and EM would learn "same household" as what a match looks like.
    # Phones are personal; an identical first + last name rules out siblings and spouses.
    t.estimate_parameters_using_expectation_maximisation(block_on("phone"), fix_u_probabilities=True)
    t.estimate_parameters_using_expectation_maximisation(block_on("first_name", "last_name"), fix_u_probabilities=True)
    pred = linker.inference.predict(threshold_match_probability=0.01)
    pairs = pred.as_pandas_dataframe()[["unique_id_l", "unique_id_r", "match_probability", "match_weight"]]
    from .cluster import constrained_clusters
    clusters = constrained_clusters(records, pairs, threshold)
    return pairs, clusters, linker
