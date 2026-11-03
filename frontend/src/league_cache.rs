//! Short-lived directory data for the current running application.
//!
//! Successful results may survive a route remount, but never a document reload.
//! Entries are partitioned by account and calendar day, expire after one minute,
//! and are invalidated by writes and explicit retry. Errors are never cached.

use crate::types::LeaguesListResponse;
use chrono::NaiveDate;
use std::cell::RefCell;

const TTL_MS: f64 = 60_000.0;

#[derive(Default)]
struct DirectoryCache<T> {
    generation: u64,
    entry: Option<(String, f64, T)>,
}

impl<T: Clone> DirectoryCache<T> {
    fn get(&self, key: &str, now: f64) -> Option<T> {
        self.entry
            .as_ref()
            .filter(|(stored, expires, _)| stored == key && now < *expires)
            .map(|(_, _, value)| value.clone())
    }

    fn store(&mut self, key: String, value: T, now: f64, generation: u64) {
        // A request started before a mutation must not repopulate the cache.
        if self.generation == generation {
            self.entry = Some((key, now + TTL_MS, value));
        }
    }

    fn invalidate(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.entry = None;
    }
}

thread_local! {
    static LEAGUES: RefCell<DirectoryCache<LeaguesListResponse>> = RefCell::new(DirectoryCache {
        generation: 0,
        entry: None,
    });
}

fn now_ms() -> f64 {
    #[cfg(target_arch = "wasm32")]
    {
        js_sys::Date::now()
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        chrono::Utc::now().timestamp_millis() as f64
    }
}

pub fn invalidate() {
    LEAGUES.with(|cache| cache.borrow_mut().invalidate());
}

pub fn peek(today: NaiveDate, account: &str) -> Option<LeaguesListResponse> {
    LEAGUES.with(|cache| cache.borrow().get(&format!("{today}:{account}"), now_ms()))
}

pub async fn load(
    today: NaiveDate,
    account: Option<String>,
) -> Result<LeaguesListResponse, String> {
    // An unavailable account check must not reuse a previous user's projection.
    let key = account.map(|account| format!("{today}:{account}"));
    let generation = LEAGUES.with(|cache| cache.borrow().generation);
    if let Some(value) = key
        .as_ref()
        .and_then(|key| LEAGUES.with(|cache| cache.borrow().get(key, now_ms())))
    {
        return Ok(value);
    }
    let value = crate::api::leagues_list(today).await?;
    if let Some(key) = key {
        LEAGUES.with(|cache| {
            cache
                .borrow_mut()
                .store(key, value.clone(), now_ms(), generation)
        });
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn route_revisits_reuse_only_matching_unexpired_account_and_day() {
        let mut cache = DirectoryCache::default();
        cache.store("2026-10-27:player:one".into(), 42, 100.0, 0);
        assert_eq!(cache.get("2026-10-27:player:one", 60_099.0), Some(42));
        assert_eq!(cache.get("2026-10-27:player:one", 60_100.0), None);
        assert_eq!(cache.get("2026-10-27:player:two", 101.0), None);
        assert_eq!(cache.get("2026-10-28:player:one", 101.0), None);
    }

    #[test]
    fn invalidation_removes_results_and_rejects_older_inflight_responses() {
        let mut cache = DirectoryCache::default();
        cache.store("anonymous".into(), 42, 0.0, 0);
        cache.invalidate();
        assert_eq!(cache.get("anonymous", 1.0), None);
        cache.store("anonymous".into(), 43, 2.0, 0);
        assert_eq!(cache.get("anonymous", 3.0), None);
        cache.store("anonymous".into(), 44, 4.0, 1);
        assert_eq!(cache.get("anonymous", 5.0), Some(44));
    }
}
