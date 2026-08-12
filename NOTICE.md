# Third-party content notice

`corpus/` and `tests/fixtures/corpus/` contain **verbatim article text from
third-party wikis**, redistributed here so the demo runs from a clean clone
without network access. No claim of authorship is made over any of it.

The code in this repository is separate from the corpus and is not covered by
this notice.

## Sources

| Source | Documents | Site | Licensing page |
|---|---:|---|---|
| Tolkien Gateway | 1,609 | <https://tolkiengateway.net> | <https://tolkiengateway.net/wiki/Tolkien_Gateway:Copyrights> |
| The Lord of the Rings Wiki (Fandom) | 639 | <https://lotr.fandom.com> | <https://www.fandom.com/licensing> |
| Wikipedia (English) | 53 | <https://en.wikipedia.org> | <https://en.wikipedia.org/wiki/Wikipedia:Copyrights> |

Counts include the `library-archive/`, `alice-private/`, and `bob-private/`
prefixes, which hold copies and holdouts drawn from the same three sources.

Every document retains a `Source:` line with the URL of the specific article it
came from, so each item is individually traceable to its origin.

## Licence status

**Verify before making this repository public.** These are the terms as
understood at the time of writing; wiki licensing changes, and the scrape did
not capture licence metadata per article.

- **Wikipedia** text is published under CC BY-SA (4.0 at time of writing) and
  the GFDL. Attribution and share-alike apply.
- **Fandom** community wiki content is published under CC BY-SA. Attribution
  and share-alike apply.
- **Tolkien Gateway** uses its own copyright policy, linked above. **This one
  has not been verified** and is the one to check first — it is also the
  largest share of the corpus by a wide margin.

Share-alike terms can extend to redistributed copies. If this repository is
published and any source turns out to prohibit redistribution, or to require
terms not met here, the corpus should be removed rather than left in place.

## Removing the corpus

The corpus is committed rather than fetched, so it lives in git history.
Deleting the files in a new commit stops it being present at HEAD but does not
remove it from earlier commits — that needs a history rewrite
(`git filter-repo` or equivalent) and a force push.

The alternative shape, if that becomes preferable: keep a small excerpt
committed so the offline clean-clone requirement still holds, and publish the
full corpus as a release asset that the seed script downloads. That was how the
pre-fork repository handled it, specifically so the corpus stayed "separately
versionable and easy to withdraw".
