# Public route indexing

[`public_pages.py`](../app/routes/public_pages.py) classifies document indexing
from route purpose and publication state. The classification also applies to
application documents served with public rendering disabled.
API authorization continues to govern the content available to each visitor.

“No directive” means the response permits normal crawler behavior. Discovery
and indexing still depend on the crawler. Public documents supply event and
league facts in the first response. Documents use `Cache-Control: private, no-store`
independently of their indexing classification.

| Route or state | Robots behavior | Publication rule |
| --- | --- | --- |
| Events/Leagues directories and published event/league Info | No directive | Public event and league permissions |
| Public directory search or page after page 1 | `noindex, follow` | Search and pagination variants retain discoverable links |
| Player/team directories | No directive | Existing public list APIs |
| Existing player/team profiles, default/Info/History tabs | No directive | Account exists; APIs apply field-level privacy |
| Player injuries/infractions and team Notes tabs | `noindex` | Panels contain account-sensitive information |
| League Info, Events/Teams tabs, and results | No directive for published leagues | `League.published` |
| Event Teams/Side events tabs and results | No directive for published events | Event publication |
| Event schedule | No directive for a published event with a published schedule | Event and schedule publication |
| Event bracket | No directive for a published event with a published bracket | Event and bracket publication |
| About, Thanks, and supported Markdown documents | No directive | Known public reading routes |
| Authentication, registration, editing, management, scoring, and other workflows | `noindex` | Workflow classification |
| Drafts, `?app=1`, `?tab=app`, unknown profiles, and unclassified panels | `noindex` | Explicit public classification required |
| Application entry missing its build, unavailable data, or missing document | `noindex` | 503/404 response |

`EventPage.indexable_app_sections` derives from publication flags. Schedule
preview permission and organizer access can expose controls to an authorized
visitor while the corresponding page remains `noindex`.

Application shells receive an explicit `noindex` value, reflected in both the
HTML meta tag and `X-Robots-Tag`. A missing build overrides it to `noindex`.
Readable public documents keep their normal status and publication metadata
when application assets are unavailable.
Public search and pagination add `follow` through their document metadata.
Application-rendered directory queries retain their route's classification.

Public reading navigation replaces document metadata with the accepted Flask
response. Application handoff removes those public metadata tags, and Layout
sets the title. Direct HTTP requests remain the source of route indexing and
canonical metadata.

Individual matches, side-competition details, legacy brackets, and additional
profile panels use the workflow classification. Expanding their indexing
requires a publication review. Sitemap generation and structured Event metadata
remain outside the current implementation.
