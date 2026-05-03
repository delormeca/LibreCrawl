# Graph Report - .  (2026-05-01)

## Corpus Check
- 29 files · ~42,376 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 690 nodes · 1518 edges · 34 communities detected
- Extraction: 64% EXTRACTED · 36% INFERRED · 0% AMBIGUOUS · INFERRED: 554 edges (avg confidence: 0.61)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]

## God Nodes (most connected - your core abstractions)
1. `WebCrawler` - 73 edges
2. `IssueDetector` - 50 edges
3. `SettingsManager` - 49 edges
4. `LinkManager` - 42 edges
5. `JavaScriptRenderer` - 37 edges
6. `MemoryMonitor` - 37 edges
7. `UserMemoryTracker` - 36 edges
8. `MemoryProfiler` - 35 edges
9. `SitemapParser` - 34 edges
10. `RateLimiter` - 33 edges

## Surprising Connections (you probably didn't know these)
- `auto_login_local_mode()` --calls--> `hash_password()`  [INFERRED]
  main.py → src/auth_db.py
- `crawl_status()` --calls--> `load_crawled_urls()`  [INFERRED]
  main.py → src/crawl_db.py
- `crawl_status()` --calls--> `load_crawl_links()`  [INFERRED]
  main.py → src/crawl_db.py
- `crawl_status()` --calls--> `load_crawl_issues()`  [INFERRED]
  main.py → src/crawl_db.py
- `embeddings_list()` --calls--> `get_embeddings_for_crawl()`  [INFERRED]
  main.py → src/crawl_db.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (64): CamoFoxRenderer, Standalone stealth browser renderer using CamoFox (patched Firefox). Does not de, Renders pages using CamoFox — a Firefox fork with C++-level anti-detection., Main web crawler orchestrator with smooth rate limiting and modular architecture, Get default configuration, Crawl a single URL using JavaScript rendering, Async crawling loop for JavaScript rendering, Update linked_from field for all crawled URLs based on collected source_pages da (+56 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (84): WebCrawler, Update target_status for all links based on crawl results, auto_login_local_mode(), cleanup_old_instances(), crawl_stats(), crawl_status(), dashboard(), debug_memory() (+76 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (64): applyFilter(), applyLinksFilter(), checkEmbeddingsExist(), checkOpenAIKey(), clearActiveFilters(), clearAllTables(), clearCrawlData(), closeExportAllModal() (+56 more)

### Community 3 - "Community 3"
Cohesion: 0.04
Nodes (35): Update crawl status     status: 'running', 'paused', 'completed', 'failed', 'st, set_crawl_status(), Check for heading-related issues, Check for content-related issues, Check for technical SEO issues, Detect SEO issues for a crawled URL, Check for mobile optimization issues, Check for accessibility issues (+27 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (62): authenticate_user(), create_user(), create_verification_token(), delete_user_settings(), get_all_users(), get_crawls_last_24h(), get_db(), get_guest_crawls_last_24h() (+54 more)

### Community 5 - "Community 5"
Cohesion: 0.05
Nodes (57): cleanup_old_crawls(), count_crawl_issues(), count_crawl_links(), count_crawled_urls(), create_crawl(), delete_crawl(), get_crashed_crawls(), get_crawl_by_id() (+49 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (24): exportData(), saveCrawl(), deleteCrawlFromDashboard(), openDashboard(), analyzeData(), onCrawlComplete(), onDataUpdate(), onTabActivate() (+16 more)

### Community 7 - "Community 7"
Cohesion: 0.12
Nodes (21): Render a page using CamoFox stealth browser.          Args:             url: The, Detect where on the page a link is placed, Check if URL is internal to the base domain, Get list of source pages that link to this URL, Extract links from HTML and add to discovery queue, Collect all links for the Links tab display, Measure a batch of link dicts., create_empty_result() (+13 more)

### Community 8 - "Community 8"
Cohesion: 0.19
Nodes (17): analyzeEEAT(), generateRecommendations(), getPercentage(), getScoreClass(), getScoreColor(), onCrawlComplete(), onDataUpdate(), onTabActivate() (+9 more)

### Community 9 - "Community 9"
Cohesion: 0.14
Nodes (4): handlePluginTabSwitch(), switchTab(), PluginLoader, register()

### Community 10 - "Community 10"
Cohesion: 0.11
Nodes (8): Start monitoring - record baseline memory, Update current memory usage and track peak, Get current process memory usage in MB, Update the rate limit dynamically, Load settings from database or return defaults, Reset settings to defaults, Initialize settings manager         user_id: Database user ID for per-user sett, Get fresh default settings

### Community 11 - "Community 11"
Cohesion: 0.16
Nodes (14): build_embedding_input(), bytes_to_embedding(), embed_pages(), embedding_to_bytes(), OpenAI embedding module for content vectorization mode., Check that OPENAI_API_KEY is set and valid. Returns (ok, error_message)., Concatenate page fields into a single embedding input string., Truncate text to max_tokens using tiktoken cl100k_base encoding. (+6 more)

### Community 12 - "Community 12"
Cohesion: 0.25
Nodes (11): applyLayout(), changeLayout(), clearVisualization(), filterVisualization(), getStatusClass(), initVisualization(), loadVisualizationData(), setupInteractions() (+3 more)

### Community 13 - "Community 13"
Cohesion: 0.22
Nodes (2): addUrlToTable(), VirtualScroller

### Community 14 - "Community 14"
Cohesion: 0.29
Nodes (1): ColumnResizer

### Community 15 - "Community 15"
Cohesion: 0.4
Nodes (1): IncrementalPoller

### Community 16 - "Community 16"
Cohesion: 1.0
Nodes (0): 

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (1): Get memory usage breakdown by object type

### Community 18 - "Community 18"
Cohesion: 1.0
Nodes (1): Backward-compat wrapper — uses shallow measurement, no recursion.

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (0): 

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (1): Extract basic SEO data (title, headings, meta description, etc.)

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (1): Extract clean main body text using pre-clean + trafilatura.          1. Pre-cl

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (1): Extract all meta tags

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (1): Extract OpenGraph meta tags

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (1): Extract Twitter Card meta tags

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (1): Extract JSON-LD structured data

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (1): Detect analytics and tracking scripts

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Extract image information with optional HEAD requests for file metadata

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Count internal vs external links

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (1): Extract hreflang links

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (1): Extract Schema.org microdata

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Extract microdata properties from an element

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Create an empty result structure

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **161 isolated node(s):** `Login as a guest user (no account required, limited to 3 crawls/24h)`, `Archive crawl (mark as archived but keep data)`, `User authentication database module Handles user registration, login, and verif`, `Context manager for database connections`, `Initialize the database with users and settings tables` (+156 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 16`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 17`** (1 nodes): `Get memory usage breakdown by object type`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (1 nodes): `Backward-compat wrapper — uses shallow measurement, no recursion.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 19`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (1 nodes): `Extract basic SEO data (title, headings, meta description, etc.)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 21`** (1 nodes): `Extract clean main body text using pre-clean + trafilatura.          1. Pre-cl`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `Extract all meta tags`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (1 nodes): `Extract OpenGraph meta tags`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (1 nodes): `Extract Twitter Card meta tags`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (1 nodes): `Extract JSON-LD structured data`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (1 nodes): `Detect analytics and tracking scripts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `Extract image information with optional HEAD requests for file metadata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `Count internal vs external links`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `Extract hreflang links`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `Extract Schema.org microdata`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `Extract microdata properties from an element`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `Create an empty result structure`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `clearAllTables()` connect `Community 2` to `Community 3`?**
  _High betweenness centrality (0.236) - this node is a cross-community bridge._
- **Why does `WebCrawler` connect `Community 1` to `Community 0`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 10`?**
  _High betweenness centrality (0.213) - this node is a cross-community bridge._
- **Why does `load_crawl_into_session()` connect `Community 5` to `Community 0`, `Community 1`, `Community 3`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Are the 44 inferred relationships involving `WebCrawler` (e.g. with `Generate a random password with letters, digits, and symbols` and `Auto-login for local mode - creates or logs into 'local' admin account`) actually correct?**
  _`WebCrawler` has 44 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `IssueDetector` (e.g. with `WebCrawler` and `Main web crawler orchestrator with smooth rate limiting and modular architecture`) actually correct?**
  _`IssueDetector` has 28 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `SettingsManager` (e.g. with `Generate a random password with letters, digits, and symbols` and `Auto-login for local mode - creates or logs into 'local' admin account`) actually correct?**
  _`SettingsManager` has 35 INFERRED edges - model-reasoned connections that need verification._
- **Are the 28 inferred relationships involving `LinkManager` (e.g. with `WebCrawler` and `Main web crawler orchestrator with smooth rate limiting and modular architecture`) actually correct?**
  _`LinkManager` has 28 INFERRED edges - model-reasoned connections that need verification._