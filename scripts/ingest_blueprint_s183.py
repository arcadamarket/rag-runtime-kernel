#!/usr/bin/env python3
"""
BLUEPRINT + RUNBOOK INGESTION DRIVER — clone birth, S183.

RUNBOOK section 5A: the blueprint is undigested state, not briefing material.
This script dismantles it into the four sanctioned destinations through the
GOVERNED VERBS ONLY (add-rule / add / context set / register-asset). It never
touches RAG_MASTER.json directly.

PY-SCRIPT-MANDATE (Rule 31): this is a script, not a runbook section, because
every step of it can execute.

Exit predicate (5A.3): after this runs, the clone answers what the blueprint
answers WITHOUT the blueprint. Verified by re-running `rag_kernel ingest` and by
moving the source document.
"""
import subprocess
import sys
import json
import tempfile
import os

TARGET = "/mnt/c/Users/pakhol/Desktop/TODAY/_ONLINE_BIZ_PROJECT"
RAGDIR = TARGET + "/RAG"
RAG = RAGDIR + "/RAG_MASTER.json"
SESSION = "S1"

FAILURES = []
OK = []


def run(args, label):
    """Run one governed verb. Fail loud, never silently."""
    p = subprocess.run(
        [sys.executable, "-m", "rag_kernel"] + args,
        cwd=RAGDIR, capture_output=True, text=True,
    )
    tag = "OK  " if p.returncode == 0 else "FAIL"
    if p.returncode == 0:
        OK.append(label)
    else:
        FAILURES.append((label, (p.stdout + p.stderr).strip()[:400]))
    print(f"[{tag}] {label}")
    if p.returncode != 0:
        print("       " + (p.stdout + p.stderr).strip().replace("\n", "\n       ")[:600])
    return p.returncode


def add_rule(key, text):
    fd, path = tempfile.mkstemp(suffix=".txt", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    rc = run(["add-rule", key, "--value-file", path, "--rag", RAG,
              "--session", SESSION, "--allow-overwrite"], f"rule {key}")
    os.unlink(path)
    return rc


def ctx_set(partition, value):
    fd, path = tempfile.mkstemp(suffix=".json", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=1, ensure_ascii=False)
    rc = run(["context", "set", partition, "--value-file", path,
              "--rag-dir", RAGDIR], f"context {partition}")
    os.unlink(path)
    return rc


def item(item_id, title, status="OPEN", note=None, kind="TASK"):
    args = ["add", item_id, title, "--rag", RAG, "--session", SESSION,
            "--status", status, "--kind", kind]
    if note:
        args += ["--note", note]
    return run(args, f"item {item_id} [{status}]")


def asset(path, purpose):
    return run(["register-asset", path, "--purpose", purpose,
                "--session", SESSION, "--rag-dir", RAGDIR,
                "--project-root", TARGET], f"asset {path}")


# ─────────────────────────────────────────────────────────────────────────────
# 1 · HOT RULES — standing invariants that govern behaviour every session
# ─────────────────────────────────────────────────────────────────────────────

RULES = {
"specifics_are_data": (
 "GOVERNING PRINCIPLE (blueprint section 0, operator-set, confirmed by every "
 "piece of evidence gathered since). PER-BUSINESS SPECIFICS ARE DATA, NEVER CODE. "
 "Adding business #7 is a manifest row. Adding township #43 is a manifest row. No "
 "business name, domain, phone number, service area or brand colour appears "
 "anywhere in this deployment's code. If it varies per instance it is manifest "
 "data. This principle is what makes the pipeline parameterised rather than a "
 "collection of hand-built sites, and it is the reason page structure is a "
 "Cartesian product over manifest data (services x locations) rather than "
 "authored per page."
),
"pipeline_stages": (
 "THE PIPELINE IS A STATE MACHINE, NOT A DIAGRAM (blueprint section 1). Stages: "
 "0 BOOTSTRAP (manifest row -> instance identity, two modes, one exit state) -> "
 "1 DRAFT (copy and pages, LLM) -> 2 PACKAGE (brand pack, fill bundle) -> "
 "3 PUBLISH (site/host, HARD GATE) -> OPERATOR APPROVAL (one yes on the whole "
 "batch) -> 4 DISTRIBUTE (API or browser adapter, unattended, resumable) -> "
 "5 MAINTAIN (posts, updates, ads, reviews). STAGE 3 IS A HARD GATE: nothing "
 "distributes until assets are live at a public HTTPS URL (see "
 "public_host_precondition). Implement as a real state machine with decidable "
 "transitions; a stage that cannot state its exit predicate is not a stage."
),
"bootstrap_modes": (
 "STAGE 0 HAS TWO IMPLEMENTATIONS AND ONE EXIT STATE (blueprint section 2). "
 "RETROFIT (business already exists: accounts, assets, domain or listings live): "
 "DISCOVER, DO NOT RE-CREATE; NEVER OVERWRITE. (1) enumerate what exists per "
 "channel, (2) record discovered state into the instance manifest, (3) fill only "
 "the gaps, (4) never regenerate an existing asset and never overwrite live "
 "listing copy without an explicit operator instruction. GREENFIELD (nothing "
 "exists): provision identity, generate brand pack, build and deploy the site, "
 "emit fill-in bundles for channels that cannot be created programmatically. "
 "BOTH EXIT TO INSTANCE_READY -- a manifest row whose channel table is fully "
 "populated with a state per channel. Stages 1-5 are then identical. This is why "
 "bootstrap is a stage with two implementations and NOT a branch buried inside "
 "drafting code."
),
"legal_identity_constant": (
 "LEGAL IDENTITY IS A CONSTANT, NOT A ROW (blueprint section 2.4, operator ruling "
 "G9). One INC, registered once, under which every business runs as an instance. "
 "CONSEQUENCE, BINDING: instance bootstrap MUST NEVER contain an incorporation "
 "step. Per-listing verification (Google Business Profile, BBB) remains "
 "per-instance and is modelled separately from the one-time entity registration."
),
"anti_thin_content": (
 "ANTI-THIN-CONTENT RULE -- MANDATORY, DECIDABLE, GATED (blueprint section 3.3). "
 "EVERY MATRIX PAGE MUST CARRY AT LEAST ONE FACT TRUE ONLY OF THAT CELL: a real "
 "township name, a named landmark, an actual job photo from that area, a "
 "verifiable local response time. A PAGE THAT CANNOT BE GIVEN A CELL-SPECIFIC "
 "FACT IS NOT GENERATED. Rationale: bulk generation of matrix leaves is where an "
 "LLM belongs and simultaneously where it is most dangerous -- 42 templated "
 "near-duplicates IS the doorway-page pattern, and at scale a de-duplication "
 "liability. Enforce as a predicate in the generator, not as an intention."
),
"fetch_backed_competitor_analysis": (
 "COMPETITOR ANALYSIS IS FETCH-BACKED OR IT DOES NOT RUN (blueprint section 3.4). "
 "Evidence: across three runs twelve named competitor domains were 'analysed' and "
 "every finding returned was one requiring no fetch (use emergency-themed "
 "keywords, include location in title, schema markup, mobile responsive). No "
 "title tag quoted, no keyword density, no backlink profile -- the model "
 "pattern-matched the category and never opened a page. REQUIREMENT: competitor "
 "analysis performs a REAL FETCH and records PER-DOMAIN EVIDENCE, or it is cut "
 "from the pipeline entirely. Under authoritative_sources_only there is no third "
 "option."
),
"no_synthetic_credibility": (
 "NO SYNTHETIC CREDIBILITY, EVER (blueprint sections 3.5 and 10). Banked as a "
 "rejection so it is never re-proposed: fabricated testimonials, invented "
 "'2K+ served' counts, placeholder review text presented as real. The standard is "
 "already set by the Chicagoland README: 'No customer testimonials exist yet "
 "since the business is new; none were fabricated for the site.' Absence of "
 "social proof is stated, never manufactured."
),
"public_host_precondition": (
 "THE PUBLIC HOST IS A PREREQUISITE, NOT A DELIVERABLE (blueprint section 4.1) -- "
 "the constraint that reorders the whole pipeline, raised by no ingested source. "
 "Meta, Instagram and Google Business Profile do NOT accept an upload; they take a "
 "PUBLIC URL and fetch it themselves: Facebook photos take `url` "
 "(POST /{page-id}/photos); Instagram takes `image_url`/`video_url` -- 'we cURL "
 "media... must be hosted on a publicly accessible server' (POST /{ig-id}/media); "
 "Google Business Profile takes `media[].sourceUrl` (POST .../localPosts). "
 "THEREFORE NOTHING CAN BE DISTRIBUTED UNTIL EVERY ASSET IS LIVE AT A PUBLIC HTTPS "
 "ADDRESS. Ordering is forced and non-negotiable: intake -> EXIF strip -> licence "
 "check -> resize -> PUBLISH TO PUBLIC HOST -> fan-out. The business's own site is "
 "the public host (operator ruling D30). A placeholder SITE_URL does not block "
 "cosmetics -- it blocks distribution entirely for that instance."
),
"asset_class_rules": (
 "ASSET CLASSES HAVE DIFFERENT RULES (blueprint section 4.2, operator ruling G7 "
 "refined): brand assets are GENERATED, job photos are REAL, stock MAY appear. "
 "(1) BRAND PACK (logo, banner, flyer, card, covers): generated one-off via "
 "free-tier image AI or the local ui-ux-pro-max-mcp; one-off brand-style "
 "development per instance; no camera, no GPS, nothing to strip. (2) REAL JOB "
 "PHOTOS (operator's phone): EXIF STRIP, LOCALLY, FAIL-CLOSED. Real job photos "
 "carry the GPS of customers' homes. AN IMAGE THAT CANNOT BE VERIFIED CLEAN DOES "
 "NOT PROCEED TO A PUBLIC HOST. (3) STOCK/LICENSED: licence provenance tracked "
 "per asset. EXIF stripping is a local tool step, not a stage -- small, and "
 "non-negotiable because the host it feeds is public."
),
"approval_boundary": (
 "THE APPROVAL BOUNDARY (blueprint section 5.1, operator ruling G6): 'You read all "
 "10 once, say yes, machine posts all 10.' Semantic quality (is this tone right, "
 "is this claim fair) cannot be formalised; 'did the POST return 200 and is the "
 "permalink live' IS a decidable predicate. Publishing is machine-checkable; taste "
 "is not. BOUNDARY: Draft -> AI, operator-approved IN BATCHES (judgment). Package "
 "-> AI + design tooling. DISTRIBUTE -> DETERMINISTIC CODE, ZERO AI (decidable "
 "predicate). HITL SITS ON THE TAIL, NOT IN THE MAIN PATH. Binding state-machine "
 "consequences: approval is ONE transition on the BATCH (DRAFTED -> APPROVED) "
 "covering all N targets; after APPROVED no target may block on human input; "
 "because nothing stops to ask, PARTIAL FAILURE IS THE NORMAL CASE; failures are "
 "reported afterwards as a list and are resumable without re-posting successes."
),
"channel_lifecycle": (
 "CHANNEL AUTOMATABILITY IS RUNTIME STATE PER BUSINESS, NEVER HARD-CODED "
 "(blueprint sections 5.2-5.4). TWO TRANSPORTS, both first-class: API ADAPTER "
 "(a posting API exists and access is granted; success predicate = HTTP status + "
 "returned id + retrievable permalink) and BROWSER ADAPTER (no API, or API is "
 "paywalled; success predicate = post visible on the rendered page after "
 "navigation). A channel is NOT 'automatable or not' -- it is 'API-transport or "
 "browser-transport'. A browser adapter that cannot confirm the post landed is a "
 "FAILED adapter, not a best-effort one. LIFECYCLE: NOT_APPLICABLE | NOT_APPLIED "
 "-> PENDING_APPROVAL -> APPROVED -> LIVE, with NOT_APPLIED -> MANUAL_REQUIRED -> "
 "LIVE. MANUAL_REQUIRED IS AN ONBOARDING STATE, NOT A TERMINAL ONE: it fires ONCE "
 "per channel per instance at account creation -- clone emits the fill-in bundle "
 "plus the manual, operator creates the account by hand once, channel transitions "
 "to LIVE, and from then on the clone OPERATES that account over the browser "
 "transport. Account creation is the manual step. Operation never is."
),
"resumable_fanout": (
 "THE FAN-OUT IS A RESUMABLE TRANSACTION, NOT A SCRIPT (blueprint section 5.5 -- "
 "the design constraint no ingested source raised). A fan-out to N channels WILL "
 "partially fail. A naive `for channel in channels: post()` re-run after a failure "
 "DOUBLE-POSTS to the channels that already succeeded, and on social accounts "
 "double-posting is reputational damage that cannot be undone. REQUIRED, same "
 "discipline as the kernel's own close transaction: (1) PER-TARGET STATE MARKER "
 "persisted BEFORE the next target begins; (2) IDEMPOTENCE KEY so a re-run "
 "recognises 'already posted'; (3) A RESUME VERB, NOT A RE-RUN; (4) per-channel "
 "decidable success predicate; (5) MANUAL_REQUIRED as a first-class terminal state "
 "producing a clean work item -- not a crash, not a silent skip. IDEMPOTENCE KEYS "
 "MUST BE OURS, NOT THE VENDOR'S: Facebook returns a post id, Instagram returns a "
 "container id then a media id, GBP returns a localPost name -- there is no common "
 "identifier shape. The key is (business, channel, content_hash), computed by us, "
 "always. MARKERS MUST BE CHANNEL-SHAPED: Instagram's two-phase flow does not fit "
 "PENDING -> SENT -> CONFIRMED; it needs PENDING -> CONTAINER_CREATED -> "
 "PUBLISHING -> CONFIRMED with a bounded status poll and a 24-hour container "
 "expiry. A uniform marker schema models Instagram wrongly."
),
"reuse_before_build_outward": (
 "REUSE-BEFORE-REWRITE APPLIES OUTWARD, BEYOND THIS DEPLOYMENT'S OWN REGISTRY "
 "(blueprint section 7, operator standing instruction). Before building any "
 "substantial component, evaluate highly-regarded free open-source options on "
 "GitHub FIRST; build from scratch only if nothing fits. Evaluate against the "
 "specific requirement that decides the call, not against the category: for the "
 "CRM/dashboard that requirement is representing 'pending my approval' as a "
 "first-class state across heterogeneous channels -- off-the-shelf CRMs model "
 "leads and deals, not an approval queue over a fan-out."
),
"failed_lookup_discipline": (
 "A FAILED LOOKUP LICENSES ONE SENTENCE, THEN ESCALATION -- NEVER A VERDICT "
 "(blueprint section 12; inherited as the clone's founding habit). Across S178 and "
 "S179 the source agent made five false calls -- capability inventory, "
 "WAIT-PRIMITIVE, the packaging gap, Claude Design, and a report render -- and "
 "EVERY ONE was a verdict issued from an incomplete lookup. It also asked the "
 "operator a question the ingest had already answered, and framed a second "
 "question around a premise that was simply wrong. THE AUTOMATED GATES CAUGHT NONE "
 "OF THEM; THE OPERATOR CAUGHT ALL OF THEM. That is the argument for keeping the "
 "operator ON THE TAIL of the loop, not for designing him out of it. MINIMAL HITL "
 "IS NOT ZERO HITL. Corollary, equally binding: do not hand the operator a "
 "decision that is yours to look up, or one the verb's own contract already "
 "settles -- that is governance by question, and it is banked as E-092/E-095."
),
}

# ─────────────────────────────────────────────────────────────────────────────
# 2 · RAG_CONTEXT — project data, referenced not loaded
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_MATRIX = {
    "_source": "BLUEPRINT_ONLINE_BIZ_CLONE.md section 6; verified S179 "
               "(G1_DISTRIBUTION_VERIFICATION_S179.md). DATA, not governance: "
               "a vendor change must not be a governance edit.",
    "live_count": 8,
    "api_count": 5,
    "browser_count": 3,
    "priority_order": ["google_business_profile", "facebook", "instagram",
                       "yelp", "nextdoor", "youtube", "mapquest", "bbb"],
    "priority_note": "Operator, S179. Adapter build order follows this ranking, "
                     "NOT the API/browser split.",
    "channels": [
        {"id": "facebook", "name": "Facebook Pages", "transport": "api",
         "endpoint": "POST /{page-id}/feed",
         "notes": "returns post id; native scheduling 10min-30d; App Review "
                  "avoidable when posting to own Page as own app's developer"},
        {"id": "instagram", "name": "Instagram", "transport": "api",
         "endpoint": "POST /{ig-id}/media then publish",
         "notes": "two-phase container -> publish; <=50 posts/24h (design to the "
                  "lower of the vendor's two stated figures); JPEG only; requires "
                  "professional account + Page Publishing Authorization"},
        {"id": "google_business_profile", "name": "Google Business Profile",
         "transport": "api", "endpoint": "POST .../localPosts",
         "notes": "Event/CTA/Offer; PATCH + DELETE supported; PRODUCT POSTS "
                  "UNSUPPORTED BY API; location must be verified"},
        {"id": "youtube", "name": "YouTube", "transport": "api",
         "endpoint": "videos.insert",
         "notes": "10,000 quota units/day - ample either way"},
        {"id": "nextdoor", "name": "Nextdoor", "transport": "api",
         "endpoint": "OAuth -> /me/profiles -> post with business profile id",
         "notes": "APPROVED (D26)"},
        {"id": "yelp", "name": "Yelp", "transport": "browser",
         "notes": "no general posting API; Fusion is read-only; review-response "
                  "API is paywalled"},
        {"id": "mapquest", "name": "MapQuest", "transport": "browser",
         "notes": "listings run through Yext since 2014; no free programmatic "
                  "path (D28)"},
        {"id": "bbb", "name": "BBB", "transport": "browser",
         "notes": "no official API; accreditation paid and G9-dependent; "
                  "accreditation deferred (D29)"},
    ],
    "excluded": [
        {"id": "x_twitter", "name": "X / Twitter", "reason":
         "NOT FREE. Verified against docs.x.com: pay-per-usage, credit-based, no "
         "subscription tier and no free tier; posts carrying a link are the "
         "expensive case. Operator ruling D25 - include only if free. Revisit "
         "only if X restores a free write tier."},
        {"id": "linkedin", "name": "LinkedIn", "reason":
         "DROPPED by operator ruling D27."},
        {"id": "catalyit", "name": "Catalyit", "reason":
         "DROPPED - insurance-agency directory, wrong vertical; entered via the "
         "prompt and was echoed uncritically by every model run (D21)."},
    ],
    "parser_warning": "Blueprint S180 fix: the two DROPPED rows were orphaned "
                      "below the table prose and rendered outside it. A parser "
                      "would have read 9 rows and silently lost both exclusions. "
                      "Exclusions are first-class here for that reason.",
}

BUNDLE_CANONICAL = {
    "_source": "BLUEPRINT section 4.3. The unit of packaging. Emitted PER "
               "INSTANCE, DETERMINISTICALLY, by the `bundle` verb - not "
               "re-invented each time.",
    "precedent": "Both existing instances converged on this artifact "
                 "independently: ECH's CHATGPT/ folder and Chicagoland's brand/ "
                 "with copy.md. brand/copy.md is the operator's fill-in bundle, "
                 "already invented by hand.",
    "contents": [
        "1. Business details block - name, address, phone, hours, categories, service area",
        "2. Logo set - SVG + PNG at required sizes",
        "3. Covers pre-sized PER DESTINATION CHANNEL",
        "4. Flyer / card / banner",
        "5. Paste-ready copy - short description, long description, keywords, category picks",
        "6. First post, written and ready",
        "7. A MANUAL - step-by-step instructions for the human filling the form",
    ],
    "item_7_note": "Item 7 is the one no prior attempt produced, and it is what "
                   "turns a manual channel from a dead end into a five-minute task.",
    "known_cover_sizes": {"facebook": "820x312", "google_business": "1024x576",
                          "social": "1200x630"},
}

INSTANCE_EVIDENCE = {
    "_source": "BLUEPRINT sections 0.1 and 8. EVIDENCE AND SEED MATERIAL - NOT "
               "the target state and NOT the mission.",
    "mission": "Bundle and spin up an unlimited number of online businesses via "
               "one curated technical pattern - tools, settings, pipelines - "
               "approved by the operator and handed over as a comprehensive INIT "
               "PROJECT PACKAGE. This deployment has standing authority to "
               "improve and further develop the package it was initialised with.",
    "instances": [
        {"name": "Emergency Computer Help",
         "exists": "domain registered, site live, CHATGPT/ asset bundle (3 logo "
                   "variants, branding sheet, 2 flyers, business card, banner, "
                   "web bundle), Yelp claimed, Meta Business Suite account",
         "stopped_at": "1 Facebook follower, 0 published posts, Instagram never "
                       "connected. Yelp photos uploaded in two bursts eleven "
                       "months apart.",
         "seo_state": "1 page, 2 H2s, no H1, no sitemap, no schema - no ranking axis"},
        {"name": "Chicagoland Property Repairs",
         "exists": "Next.js 16 site (App Router, TS, Tailwind v4); brand/ with "
                   "logo SVG+PNG and covers pre-sized for Google Business / "
                   "Facebook / social; brand/copy.md with paste-ready "
                   "descriptions, keywords and category picks",
         "stopped_at": "NOT DEPLOYED - SITE_URL is a placeholder pending domain; "
                       "/api/quote only logs server-side"},
    ],
    "diagnosis": "Both bundles are good. Both were made BY HAND, ONCE, PER "
                 "BUSINESS. Neither reached an audience. The failure is not "
                 "capability - it is REPEATABILITY and DISTRIBUTION. This "
                 "deployment exists to mechanise exactly those two things and "
                 "nothing else. The folder's real significance is negative: it is "
                 "the second hand-built instance in a row.",
    "ranking_benchmarks": [
        {"site": "Epiclean", "axis": "geography",
         "pages": "46 service areas (4 parent + 42 sub-city)"},
        {"site": "BRO Coffee", "axis": "occasion", "pages": "12 events + 6 menu"},
    ],
    "born": {"session": "S183", "from": "RAG Runtime Kernel",
             "runtime": "0.4.49", "spec": "3.2.8",
             "path": "init -> birth-adopt adopt (29 universal rules, "
                     "meta.rule_provenance stamped)"},
}

# ─────────────────────────────────────────────────────────────────────────────
# 3 · TRACKED ITEMS
# ─────────────────────────────────────────────────────────────────────────────

CLOSED_DECISIONS = [
    ("D21-DROP-CATALYIT", "Catalyit dropped as a distribution target",
     "CLOSED - wrong vertical, an insurance-agency directory that entered via the "
     "prompt and was echoed uncritically by every model run. Closed to re-litigation."),
    ("D25-X-TWITTER-EXCLUDED", "X/Twitter excluded from the channel set",
     "CLOSED - not free. Verified against docs.x.com: pay-per-usage, credit-based, "
     "no free write tier; link posts are the expensive case. Revisit only if X "
     "restores a free write tier."),
    ("D26-NEXTDOOR-API", "Nextdoor API access approved",
     "CLOSED - approved. OAuth -> /me/profiles -> post with business profile id."),
    ("D27-LINKEDIN-DROPPED", "LinkedIn dropped as a channel",
     "CLOSED - channel dropped by operator ruling."),
    ("D28-MAPQUEST-BROWSER", "MapQuest served by browser transport, not paid Yext",
     "CLOSED - listings run through Yext since 2014; no free programmatic path, so "
     "browser transport instead."),
    ("D29-BBB-BROWSER", "BBB served by browser transport; accreditation deferred",
     "CLOSED - no official API; paid accreditation is G9-dependent and deferred."),
    ("D30-PUBLIC-ASSET-HOST", "The public asset host is the business's own site",
     "CLOSED - the business's own site is the public host that satisfies "
     "public_host_precondition."),
]

OPEN_DECISIONS = [
    ("D22-GENERATOR-PER-ASSET-TYPE", "Generator per asset type: one or many?",
     "OPEN - resolve during build (blueprint section 11)."),
    ("D23-COMPETITOR-FETCH-OR-CUT",
     "Competitor analysis: fetch-backed or cut from the pipeline",
     "OPEN - resolve during build. The rule is already binding "
     "(fetch_backed_competitor_analysis); this decides whether the stage exists at all."),
    ("D31-CRM-ADOPT-OR-BUILD", "CRM/dashboard: adopt an OSS option or build",
     "OPEN - research task, not an operator question. Evaluate free OSS options "
     "on GitHub first against the deciding requirement: representing 'pending my "
     "approval' as a first-class state across heterogeneous channels."),
]

REJECTIONS = [
    ("REJ-VENDOR-UI-BEHAVIOUR", "Behavioural layer in vendor UI config",
     "DISCARDED - creates a second source of truth outside the RAG, the exact "
     "side-store drift auditing exists to reject. Do not re-propose."),
    ("REJ-SYNTHETIC-CREDIBILITY", "Synthetic credibility of any kind",
     "DISCARDED - fabricated testimonials, invented counts, placeholder reviews "
     "shown as real. Do not re-propose."),
    ("REJ-ORACLE-ALWAYS-FREE", "Oracle Always Free as infrastructure",
     "DISCARDED - halved to 2 OCPU / 12 GB in 2026; the cited 4 OCPU / 24 GB "
     "figure is false. Do not re-propose."),
    ("REJ-CATALYIT-TARGET", "Catalyit as a distribution target",
     "DISCARDED - wrong vertical, a hallucination inherited through the prompt. "
     "Do not re-propose."),
    ("REJ-GENERIC-AI-COPY", "Generic AI copy as a ranking strategy",
     "DISCARDED - same prompt produces the same page as every competitor; at "
     "scale a de-duplication liability. Do not re-propose."),
]

PY_VERBS = [
    ("VERB-MANIFEST", "py verb: manifest - instance rows",
     "The manifest is the single place per-business specifics live "
     "(specifics_are_data). Decidable predicate: a row round-trips and its "
     "channel table is fully populated."),
    ("VERB-MATRIX", "py verb: matrix - services x locations page generation",
     "Cartesian product over manifest data with the anti_thin_content refusal "
     "WIRED IN, not documented: a cell with no cell-specific fact is refused, not "
     "generated."),
    ("VERB-BUNDLE", "py verb: bundle - the section 4.3 emitter including the human manual",
     "Emits all seven canonical bundle items per instance deterministically. Item "
     "7 (the manual) is what turns a manual channel into a five-minute task."),
    ("VERB-EXIF-STRIP", "py verb: exif-strip - fail-closed local EXIF removal",
     "Real job photos carry the GPS of customers' homes. An image that cannot be "
     "verified clean does not proceed to a public host. Fail-closed, never "
     "best-effort."),
    ("VERB-PUBLISH", "py verb: publish - the section 4.1 public-host gate",
     "Enforces the forced ordering intake -> EXIF strip -> licence check -> "
     "resize -> PUBLISH -> fan-out. Nothing distributes before this returns a "
     "live public HTTPS URL."),
    ("VERB-FANOUT", "py verb: fanout - resumable transaction with per-target markers",
     "Per-target marker persisted before the next target begins; idempotence key "
     "(business, channel, content_hash); a RESUME verb, not a re-run; "
     "channel-shaped markers (Instagram needs four states, not three)."),
    ("VERB-ADAPTER", "py verb: adapter - API and browser, one success predicate",
     "Both transports are first-class and must satisfy the same decidable success "
     "predicate. A browser adapter that cannot confirm the post landed is a failed "
     "adapter."),
    ("VERB-INVENTORY", "py verb: inventory - root scan + register-asset wiring",
     "So nothing is ever re-derived by grep - the failure that left the source "
     "kernel's registry empty for 178 sessions."),
    ("VERB-CHANNELS", "py verb: channels - read the matrix from RAG_CONTEXT.json",
     "The channel matrix is DATA. A vendor change must not be a governance edit."),
]

CARRIED_ITEMS = [
    ("CHICAGOLAND-SITE-URL-BLOCKER",
     "Chicagoland instance cannot distribute: SITE_URL is a placeholder",
     "Blocks distribution ENTIRELY for that instance under "
     "public_host_precondition - not a cosmetic gap. Pending domain registration."),
    ("DOMAIN-REGISTRATION-UNOWNED",
     "Domain registration is a paid manual step that no pipeline stage owns",
     "OPEN OPERATOR QUESTION carried from RUNBOOK section 10 q5: it blocks the "
     "section 4.1 public-host gate for EVERY instance and no stage owns it. The "
     "last unresolved question of the six."),
    ("PI-TRANSPORT-BLOCK-PASTE",
     "Paste PROJECT_INSTRUCTIONS.md into the project's Project Instructions tab",
     "The transport ladder, the three-case tmux triage and the wait-for verb "
     "cannot be learned from the RAG (reading it requires a governed boot, which "
     "requires a transport). Authored to PROJECT_INSTRUCTIONS.md at birth; the "
     "operator must paste it."),
    ("DASHBOARD-APPROVAL-QUEUE",
     "Dashboard reads the per-target markers the fan-out already writes",
     "Free by construction - requires no new instrumentation, only a view. "
     "Blocked by D31 (adopt vs build)."),
]

ASSETS = [
    ("RAG/BLUEPRINT_ONLINE_BIZ_CLONE.md",
     "Zero-session blueprint, INGESTED at S1 and SUPERSEDED AS A SOURCE by this "
     "RAG. Historical evidence only - the RAG answers what it answers."),
    ("RAG/RUNBOOK_CLONE_INIT_S179.md",
     "Birth runbook rev-4 (source kernel S183): the measured init -> birth-adopt "
     "procedure that created this deployment. Provenance evidence, not a live "
     "procedure."),
    ("PROJECT_INSTRUCTIONS.md",
     "Pre-boot transport block + boot command + mission. The one document that "
     "cannot live in the RAG, because reading the RAG requires a transport."),
    ("brand/logo-icon.svg", "Chicagoland brand: vector logo icon (seed instance asset)"),
    ("brand/logo-icon-square-1024.png", "Chicagoland brand: square logo icon 1024px"),
    ("brand/logo-lockup-1600x400.png", "Chicagoland brand: horizontal logo lockup"),
    ("brand/cover-facebook-820x312.png", "Chicagoland brand: Facebook cover, pre-sized 820x312"),
    ("brand/cover-google-business-1024x576.png",
     "Chicagoland brand: Google Business cover, pre-sized 1024x576"),
    ("brand/cover-social-1200x630.png", "Chicagoland brand: generic social cover 1200x630"),
    ("brand/copy.md",
     "THE PROTOTYPE FILL-IN BUNDLE - paste-ready descriptions, keywords and "
     "category picks, invented by hand. The `bundle` verb must emit this shape "
     "per instance."),
    ("docs/OPERATOR_INTAKE_QUIZ.md",
     "Operator intake questionnaire - the manifest-row source for a new instance."),
    ("web/package.json",
     "Next.js 16 seed site (App Router, TS, Tailwind v4) for the Chicagoland "
     "instance - entry point of the web/ tree. Seed material, not the target "
     "state. register-asset takes files, not directories."),
    ("web/README.md",
     "Seed site readme - records that SITE_URL is a placeholder and /api/quote "
     "only logs server-side (the CHICAGOLAND-SITE-URL-BLOCKER evidence)."),
    ("README.md", "Project readme carried from the pre-birth folder."),
]


def main():
    # `--items-only` re-lands just the tracked items and the web assets after a
    # partial run (the first S183 pass failed 16 records on kind=DECISION, which
    # this kernel does not define, and on register-asset being given a directory).
    items_only = "--items-only" in sys.argv

    print("=" * 68)
    print("BLUEPRINT INGESTION — clone S1 (RUNBOOK 5A)"
          + ("  [ITEMS-ONLY RETRY]" if items_only else ""))
    print("=" * 68)

    if not items_only:
        print("\n--- 1. HOT RULES -> operating_protocol ---")
        for key, text in RULES.items():
            add_rule(key, text)

        print("\n--- 2. RAG_CONTEXT -> non-loaded data store ---")
        ctx_set("channel_matrix", CHANNEL_MATRIX)
        ctx_set("bundle_canonical", BUNDLE_CANONICAL)
        ctx_set("instance_evidence", INSTANCE_EVIDENCE)

    print("\n--- 3. TRACKED ITEMS ---")
    for iid, title, note in CLOSED_DECISIONS:
        item(iid, title, status="RESOLVED", note=note, kind="TASK")
    for iid, title, note in OPEN_DECISIONS:
        item(iid, title, status="OPEN", note=note, kind="TASK")
    for iid, title, note in REJECTIONS:
        item(iid, title, status="DISCARDED", note=note, kind="TASK")
    if not items_only:
        for iid, title, note in PY_VERBS:
            item(iid, title, status="OPEN", note=note)
        for iid, title, note in CARRIED_ITEMS:
            item(iid, title, status="OPEN", note=note)

    print("\n--- 4. ROOT INVENTORY -> register-asset ---")
    for path, purpose in ASSETS:
        if items_only and not path.startswith("web/"):
            continue
        asset(path, purpose)

    print("\n" + "=" * 68)
    print(f"landed: {len(OK)}   failed: {len(FAILURES)}")
    for label, err in FAILURES:
        print(f"  FAIL {label}: {err}")
    print("=" * 68)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
