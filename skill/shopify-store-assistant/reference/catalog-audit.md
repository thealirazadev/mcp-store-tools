# Catalog audit

A listing-quality report for the merchant, built only from what shoppers can see.

## 1. Collect

1. `list_collections` and note each handle and title.
2. Walk every product: `browse_products(sort="title", limit=50)`, then pass `next_cursor` back
   as `cursor` with the same sort until it is `null`. Stop after 20 pages (1,000 products) and
   ask the user before continuing; large catalogs are slow and use many tokens.
3. To learn which products sit in no collection, page through each collection the same way and
   compare handles. Skip this step when there are more than 10 collections, unless the user
   asks for it.

## 2. Check each product

| Check | Flag when |
| --- | --- |
| Description | empty, or under 100 characters |
| Product type | empty |
| Vendor | empty |
| Tags | none |
| Online store | `url` is empty (published to other channels but not the online store) |
| Availability | `available` is false (fully sold out) |
| Price | a range where the top price is more than 3x the bottom (check for a typo variant) |
| Title | ALL CAPS, longer than 70 characters, or duplicated by another product |

Use `get_product` only for products you are about to recommend fixing in detail (for example to
name the sold-out variants); calling it for every product is slow.

## 3. Report

Start with a summary table: total products, and the count and percentage failing each check.
Then list the products for each failing check, worst first, with title and link, capped at 25
per check (state how many more there are). End with the three fixes that would help the most
products, in plain words.

Report only what the data shows. The Storefront API cannot see drafts, inventory counts, SEO
fields, images, or sales; say this once at the end so the merchant knows the audit's scope.
