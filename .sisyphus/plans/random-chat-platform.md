# Random Chat Platform — Weekend Prototype → Production

## TL;DR

> **Quick Summary**: Build an adults-only random video chat platform that outcompetes Chatrandom/Flingster/Shagle by offering a better product at lower cost. Weekend prototype first (Next.js + PeerJS + Redis), then production hardening.
>
> **Deliverables**:
> - Working video chat with skip/next, interest tags, region filtering (all FREE)
> - Premium tier: Reconnect + Priority Matching ($9.99/mo)
> - Age gate (prototype: checkbox; production: third-party verification)
> - Client-side AI moderation + NCMEC reporting pipeline
> - US-only geo-fencing
> - E2E encrypted messaging
> - Adult payment processing (CCBill)
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES — 5 waves
> **Critical Path**: T1 → T3 → T6 → T12/T13 → T16 → T22 → T25 → F1-F4

---

## Context

### Original Request
"I think there is a fair bit of money to be made in the most predatory way possible, in some kind of 'omegle' service" — evolved into a well-researched adults-only random video chat platform with ethical positioning.

### Interview Summary
**Key Discussions**:
- Competitive landscape: Chatrandom/Flingster/Shagle/Chatspin are ONE company (Social Media Ventures LLC), same backend, different skins. $20/month for gender filter + location filter + no ads.
- Legal liability: Section 230 is strong shield. OnlyFans won even with 20% revenue cut. Pornhub lost only because they actively curated/tagged illegal content. Don't do that.
- Architecture: P2P WebRTC (video never touches servers) + E2E encryption for messages + client-side AI scanning for moderation.
- Paid features: Reconnect (undo accidental skip within 30s) + Priority Matching (skip queue during peak). Neither degrades free experience.
- Cold start: Influencer marketing (recruit ~100 attractive women to promote on social media). OnlyFans playbook: creators bring audiences, $0 CAC.
- Unit economics: Infra cost ~$0.04-0.08/user/month (P2P = you're a signaling conduit, not a video host). At 5% conversion and $9.99/month, margins are 6-14x.

**Research Findings**:
- Cloudflare TURN: $0.05/GB with 1TB free tier — cheapest managed TURN
- Self-hosted coturn: ~80% cheaper at scale but requires ops
- OnlyFans grew from 100K to 120M users with near-zero paid marketing
- Emerald Chat: free random chat with interest matching, anti-bot measures, $5.89 Gold / $12.89 Platinum
- PeerJS abstracts WebRTC signaling for prototype; production needs custom signaling server
- Section 230 + adults-only positioning + age verification = strong legal position

### Metis Review
**Identified Gaps (addressed)**:
- **Age verification method**: Added as explicit task — checkbox for prototype, third-party for production
- **Payment processor**: Stripe won't touch adult content. Added CCBill/Epoch integration task.
- **Vercel + real-time**: Vercel doesn't support WebSocket natively. Added custom PeerJS signaling server task.
- **TURN cost modeling**: Added circuit breaker — audio-only fallback if TURN >20%. Modeled at 10/20/30%.
- **Legal counsel on 2257/FOSTA**: Added as explicit production task. Not a prototype blocker.
- **Client-side AI moderation unknowns**: Added NSFW.js smoke test as validation task.
- **IP exposure in P2P**: Documented as acceptable risk with mitigation (recommend VPN in ToS).
- **Edge cases**: Added reconnect rate limiting, region fallback expansion, queue race conditions, report flow.

---

## Work Objectives

### Core Objective
Build an adults-only random video chat platform that solves the "Snapchat problem" (users leaving for external messaging) by keeping everything in-platform, while undercutting incumbents on price and beating them on product quality.

### Concrete Deliverables
- Next.js web app with age gate, random video chat, skip/next, interest tags, region filtering
- PeerJS custom signaling server (not the public one)
- Redis-backed match queue with priority tier
- Reconnect feature (30s window, Redis TTL, rate-limited)
- Priority matching (paid users skip queue)
- Client-side NSFW detection + report/block flow
- NCMEC CyberTipline reporting pipeline
- US-only geo-fencing
- CCBill adult payment integration
- E2E encrypted in-call messaging (Signal Protocol)

### Definition of Done
- [ ] Two users can connect via random video chat, see/hear each other, and skip to next match
- [ ] Interest tags and region filtering work and affect matching
- [ ] Premium users can reconnect within 30s of accidental skip
- [ ] Premium users skip queue during peak hours
- [ ] Age verification gate blocks access before chat
- [ ] Client-side moderation flags inappropriate content
- [ ] US-only enforcement blocks non-US IPs
- [ ] Payment flow completes end-to-end for premium subscription

### Must Have
- Random video chat with skip/next (FREE)
- Interest tag matching (FREE)
- Region filtering with fallback expansion (FREE)
- Reconnect within 30s window (PAID)
- Priority matching during peak (PAID)
- Age verification gate
- Client-side AI moderation (CSAM + violence detection)
- NCMEC reporting pipeline
- US-only enforcement
- E2E encrypted messaging
- No server-side video storage — architectural invariant
- No content curation/tagging — Section 230 neutrality

### Must NOT Have (Guardrails)
- NO social features in v1 (friends, followers, profiles) — changes product category
- NO recording, clip saving, or screenshot features — legal liability
- NO content curation, algorithmic promotion, or editorial features — Section 230 risk
- NO server-side video storage or metadata persistence beyond matching TTL
- NO multi-user group rooms — different architecture entirely
- NO advanced AI matching beyond interest tags — scope creep
- NO creator tools (streaming, tipping, fan clubs) — different product
- NO international expansion in v1 — US-only

---

## Verification Strategy (MANDATORY)

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO (new project)
- **Automated tests**: Tests-after (prototype first, tests for critical paths)
- **Framework**: bun test (matches Next.js ecosystem)
- **Agent-Executed QA**: ALWAYS (mandatory for all tasks)

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Web UI**: Use Playwright — navigate, interact, assert DOM, screenshot
- **API/Backend**: Use Bash (curl) — send requests, assert status + response fields
- **Real-time**: Use interactive_bash (tmux) — run servers, test WebSocket connections
- **Integration**: Use Bash — end-to-end flow testing

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — start immediately):
├── T1: Project scaffolding + Next.js + TypeScript + Tailwind + Redis setup [quick]
├── T2: Type definitions + shared schemas [quick]
├── T3: Custom PeerJS signaling server [deep]
├── T4: Redis data model + match queue engine [unspecified-high]
├── T5: Age gate UI (checkbox for prototype, third-party stub) [quick]
├── T6: IP geolocation service + US-only enforcement [quick]

Wave 2 (Core Video Chat — after Wave 1):
├── T7: PeerJS WebRTC video chat component (depends: T1, T3) [visual-engineering]
├── T8: Match queue consumer + skip/next flow (depends: T4, T3) [unspecified-high]
├── T9: Interest tag matching with weighted overlap (depends: T2, T4) [unspecified-high]
├── T10: Region filtering with fallback expansion (depends: T2, T6, T4) [unspecified-high]
├── T11: In-call text chat UI (depends: T7, T2) [visual-engineering]
├── T12: Reconnect feature — 30s TTL + rate limiting (depends: T4, T8) [deep]

Wave 3 (Premium + Payment — after Wave 2 core):
├── T13: Priority matching — paid users skip queue (depends: T4, T8) [unspecified-high]
├── T14: CCBill adult payment integration (depends: T2) [deep]
├── T15: Premium badge/indicator UI + subscription management (depends: T14) [visual-engineering]
├── T16: Client-side NSFW detection (NSFW.js) + report/block flow (depends: T7) [unspecified-high]
├── T17: NCMEC CyberTipline reporting pipeline (depends: T16) [unspecified-high]
├── T18: Rate limiting + bot detection + multi-account prevention (depends: T4) [unspecified-high]

Wave 4 (Production Hardening — after Wave 3):
├── T19: Third-party age verification (Veriff/Yoti integration) (depends: T5, T14) [deep]
├── T20: E2E encrypted messaging — Signal Protocol (depends: T11) [deep]
├── T21: TURN relay setup (Cloudflare TURN or self-hosted coturn) (depends: T3) [unspecified-high]
├── T22: Connection quality handling — adaptive bitrate, audio fallback (depends: T7, T21) [unspecified-high]
├── T23: Landing page + marketing site (depends: T1) [visual-engineering]
├── T24: Influencer onboarding flow + referral program (depends: T14) [unspecified-high]

Wave 5 (Deployment + Polish — after Wave 4):
├── T25: Production deployment pipeline (Vercel + dedicated signaling server) (depends: T3, T21) [quick]
├── T26: Monitoring + alerting + TURN cost circuit breaker (depends: T4, T21) [unspecified-high]
├── T27: Legal compliance review — ToS, privacy policy, 2257 assessment (depends: T17, T19) [writing]

Wave FINAL (Verification — after ALL tasks):
├── F1: Plan compliance audit (oracle)
├── F2: Code quality review (unspecified-high)
├── F3: Real manual QA (unspecified-high + playwright)
├── F4: Scope fidelity check (deep)
→ Present results → Get explicit user okay

Critical Path: T1 → T3 → T7 → T8 → T12 → T16 → T22 → T25 → F1-F4
Parallel Speedup: ~60% faster than sequential
Max Concurrent: 6 (Waves 2-3)
```

### Dependency Matrix (abbreviated)

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T7, T23 |
| T2 | — | T9, T10, T11 |
| T3 | — | T7, T8, T21, T25 |
| T4 | — | T8, T9, T10, T12, T13, T18, T26 |
| T5 | — | T19 |
| T6 | — | T10 |
| T7 | T1, T3 | T11, T16, T22 |
| T8 | T4, T3 | T12, T13 |
| T9 | T2, T4 | — |
| T10 | T2, T6, T4 | — |
| T11 | T7, T2 | T20 |
| T12 | T4, T8 | — |
| T13 | T4, T8 | — |
| T14 | T2 | T15, T24 |
| T15 | T14 | — |
| T16 | T7 | T17 |
| T17 | T16 | T27 |
| T18 | T4 | — |
| T19 | T5, T14 | T27 |
| T20 | T11 | — |
| T21 | T3 | T22, T25, T26 |
| T22 | T7, T21 | — |
| T23 | T1 | — |
| T24 | T14 | — |
| T25 | T3, T21 | — |
| T26 | T4, T21 | — |
| T27 | T17, T19 | — |

### Agent Dispatch Summary

- **Wave 1**: 6 tasks — T1,T2,T5,T6 → `quick`, T3 → `deep`, T4 → `unspecified-high`
- **Wave 2**: 6 tasks — T7,T11 → `visual-engineering`, T8,T9,T10 → `unspecified-high`, T12 → `deep`
- **Wave 3**: 6 tasks — T13,T16,T17,T18 → `unspecified-high`, T14 → `deep`, T15 → `visual-engineering`
- **Wave 4**: 6 tasks — T19,T20 → `deep`, T21,T22,T24 → `unspecified-high`, T23 → `visual-engineering`
- **Wave 5**: 3 tasks — T25 → `quick`, T26 → `unspecified-high`, T27 → `writing`
- **FINAL**: 4 tasks — F1 → `oracle`, F2 → `unspecified-high`, F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, curl endpoint, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run TypeScript compiler + linter + test suite. Review all changed files for: `as any`/`@ts-ignore`, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify no video/chat data persistence beyond matching TTL.
  Output: `Build [PASS/FAIL] | Lint [PASS/FAIL] | Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Real Manual QA** — `unspecified-high` + `playwright` skill
  Start from clean state. Execute EVERY QA scenario from EVERY task — follow exact steps, capture evidence. Test cross-task integration (matching → video → skip → reconnect → payment flow). Test edge cases: empty state, no matches in region, rapid skip, mid-call report. Save to `.sisyphus/evidence/final-qa/`.
  Output: `Scenarios [N/N pass] | Integration [N/N] | Edge Cases [N tested] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT Have" compliance: no social features, no recording, no content curation, no video storage, no group rooms. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `feat(core): project scaffolding, types, signaling, queue, age gate, geo-fence` — 6 files
- **Wave 2**: `feat(chat): video chat, matching, tags, region, text chat, reconnect` — 8 files
- **Wave 3**: `feat(premium): priority matching, CCBill payment, premium UI, moderation, reporting, rate limiting` — 8 files
- **Wave 4**: `feat(hardening): age verification, E2E messaging, TURN relay, adaptive quality, landing page, referrals` — 8 files
- **Wave 5**: `feat(deploy): production pipeline, monitoring, legal compliance` — 4 files

---

## Success Criteria

### Verification Commands
```bash
# Build succeeds
npm run build  # Expected: clean build, no errors

# Type check passes
npx tsc --noEmit  # Expected: 0 errors

# Core flow works
curl http://localhost:3000/api/health  # Expected: 200 OK

# Match queue operates
redis-cli LLEN match:queue:free  # Expected: integer ≥ 0

# PeerJS signaling responds
curl http://localhost:3001/peers  # Expected: peer list or empty array
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Two users can video chat end-to-end
- [ ] Skip/next works and returns to queue
- [ ] Interest tags affect matching
- [ ] Region filtering works with fallback
- [ ] Reconnect works within 30s window
- [ ] Priority matching places paid users ahead
- [ ] Age verification gate blocks access
- [ ] Client-side moderation flags content
- [ ] US-only enforcement blocks non-US IPs
- [ ] No video/chat data persists after session end