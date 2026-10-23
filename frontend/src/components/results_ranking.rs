use crate::types::TeamResultRow;
use std::cmp::Ordering;
use std::collections::HashMap;

fn match_count(team: &TeamResultRow) -> u64 {
    u64::from(team.matches_won) + u64::from(team.matches_lost)
}

fn compare_fraction(a_won: u32, a_total: u64, b_won: u32, b_total: u64) -> Ordering {
    // No recorded points means 0%, including when compared with recorded losses.
    let a_denominator = u128::from(a_total.max(1));
    let b_denominator = u128::from(b_total.max(1));
    (u128::from(a_won) * b_denominator).cmp(&(u128::from(b_won) * a_denominator))
}

/// Compare match win percentage, then point win percentage, best first.
///
/// A stronger `a` returns `Less`, so this can be passed directly to `sort_by`.
/// Teams without a decided match sort last. Equal percentages remain equal;
/// names, raw wins, and games played do not break standings ties.
pub fn compare_stats(a: &TeamResultRow, b: &TeamResultRow) -> Ordering {
    let a_matches = match_count(a);
    let b_matches = match_count(b);
    match (a_matches == 0, b_matches == 0) {
        (true, true) => return Ordering::Equal,
        (true, false) => return Ordering::Greater,
        (false, true) => return Ordering::Less,
        (false, false) => {}
    }

    compare_fraction(a.matches_won, a_matches, b.matches_won, b_matches)
        .then_with(|| {
            compare_fraction(
                a.points_won,
                u64::from(a.points_won) + u64::from(a.points_lost),
                b.points_won,
                u64::from(b.points_won) + u64::from(b.points_lost),
            )
        })
        .reverse()
}

/// Calculate competition ranks (1, 1, 3) independently of display order.
/// Teams without a decided match are omitted from the returned map.
pub fn calculate_ranks(teams: &[TeamResultRow]) -> HashMap<String, usize> {
    let mut played: Vec<_> = teams.iter().filter(|team| match_count(team) > 0).collect();
    played.sort_by(|a, b| compare_stats(a, b).then_with(|| a.id.cmp(&b.id)));

    let mut ranks = HashMap::with_capacity(played.len());
    let mut rank = 0;
    for (index, team) in played.iter().enumerate() {
        if index == 0 || compare_stats(played[index - 1], team) != Ordering::Equal {
            rank = index + 1;
        }
        ranks.insert(team.id.clone(), rank);
    }
    ranks
}

#[cfg(test)]
mod tests {
    use super::*;

    fn team(id: &str, matches: (u32, u32), points: (u32, u32)) -> TeamResultRow {
        TeamResultRow {
            id: id.to_owned(),
            pseudonym: id.to_owned(),
            shortname: None,
            profile_photo: None,
            matches_won: matches.0,
            matches_lost: matches.1,
            points_won: points.0,
            points_lost: points.1,
        }
    }

    #[test]
    fn match_percentage_takes_priority_over_raw_wins_and_points() {
        let unbeaten = team("unbeaten", (1, 0), (1, 9));
        let more_wins = team("more-wins", (9, 1), (100, 0));
        assert_eq!(compare_stats(&unbeaten, &more_wins), Ordering::Less);
        assert_eq!(calculate_ranks(&[more_wins, unbeaten])["unbeaten"], 1);
    }

    #[test]
    fn point_percentage_breaks_equal_match_percentage() {
        let better_points = team("better-points", (1, 1), (1, 0));
        let more_matches = team("more-matches", (2, 2), (9, 1));
        assert_eq!(compare_stats(&better_points, &more_matches), Ordering::Less);
        let ranks = calculate_ranks(&[more_matches, better_points]);
        assert_eq!(ranks["better-points"], 1);
        assert_eq!(ranks["more-matches"], 2);
    }

    #[test]
    fn equal_ratios_with_different_totals_share_competition_ranks() {
        let a = team("a", (1, 1), (1, 3));
        let b = team("b", (2, 2), (2, 6));
        assert_eq!(compare_stats(&a, &b), Ordering::Equal);
        let ranks = calculate_ranks(&[team("third", (1, 2), (100, 0)), b, a]);
        assert_eq!(ranks["a"], 1);
        assert_eq!(ranks["b"], 1);
        assert_eq!(ranks["third"], 3);
    }

    #[test]
    fn teams_without_decided_matches_are_unranked_even_with_points() {
        let unplayed = team("unplayed", (0, 0), (100, 0));
        let lost = team("lost", (0, 1), (0, 1));
        assert_eq!(compare_stats(&unplayed, &lost), Ordering::Greater);
        let ranks = calculate_ranks(&[unplayed.clone(), lost]);
        assert_eq!(ranks.len(), 1);
        assert_eq!(ranks["lost"], 1);
        assert!(calculate_ranks(&[unplayed]).is_empty());
        assert!(calculate_ranks(&[]).is_empty());
    }

    #[test]
    fn no_recorded_points_are_zero_percent() {
        let no_points = team("no-points", (1, 1), (0, 0));
        let only_losses = team("only-losses", (1, 1), (0, 10));
        let some_points = team("some-points", (1, 1), (1, 10));
        assert_eq!(compare_stats(&no_points, &only_losses), Ordering::Equal);
        assert_eq!(compare_stats(&some_points, &no_points), Ordering::Less);
    }

    #[test]
    fn input_order_and_display_names_do_not_change_ranks() {
        let mut teams = vec![
            team("a", (1, 1), (1, 3)),
            team("b", (2, 2), (2, 6)),
            team("c", (1, 2), (100, 0)),
        ];
        let expected = calculate_ranks(&teams);
        teams.reverse();
        teams[0].pseudonym = "First alphabetically".to_owned();
        teams[1].pseudonym = "ZZZ".to_owned();
        assert_eq!(calculate_ranks(&teams), expected);
    }

    #[test]
    fn high_counts_are_exact_without_overflow_or_float_rounding() {
        let max = u32::MAX;
        // Adjacent ratios differ by less than f64 precision near one half.
        let a = team("a", (max, max - 1), (max, max));
        let b = team("b", (max - 1, max - 2), (0, max));
        assert_eq!(compare_stats(&b, &a), Ordering::Less);
        assert_eq!(calculate_ranks(&[a, b])["b"], 1);

        let a = team("a", (max, max), (max, max - 1));
        let b = team("b", (max, max), (max - 1, max - 2));
        assert_eq!(compare_stats(&b, &a), Ordering::Less);
        assert_eq!(calculate_ranks(&[a, b])["b"], 1);
    }
}
