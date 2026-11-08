# Catalog audit

A listing-quality report for the merchant, built only from what shoppers can see.

## 1. Run it

Call `audit_catalog`. It walks up to `max_products` published products (default 1,000) in the
server and returns, per check, a `label`, `count`, `percent`, and up to `examples_per_check`
products with links, plus `top_issues` and a `scope` note. If `truncated` is true, say how many
products were checked and offer to run it again with a higher `max_products` (up to 5,000).

| Check | Flags a product when |
| --- | --- |
| `short_description` | the description is empty or under 100 characters |
| `no_image` | it has no image |
| `no_product_type`, `no_vendor`, `no_tags` | that field is empty |
| `not_on_online_store` | it has no product URL (published to other channels only) |
| `sold_out` | no variant is available |
| `wide_price_range` | the highest variant price is over 3x the lowest (check for a typo) |
| `title_all_caps`, `title_too_long` | the title is in ALL CAPS or over 70 characters |
| `duplicate_title` | another product has the same title |

## 2. Report

Start with a summary table of every check with a count above zero: label, count, percent.
Then list the example products per check, worst first, with title and link. End with the three
fixes from `top_issues` that would help the most products, in plain words, and the `scope` note
so the merchant knows the audit cannot see drafts, inventory counts, SEO fields, or sales.

Use `get_product` only for products you are about to recommend fixing in detail (for example to
name the sold-out variants).
