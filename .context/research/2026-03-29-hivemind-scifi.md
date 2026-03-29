# Research: Hivemind and Collective Consciousness in Science Fiction
## Design Principles for the Memesis Memory Lifecycle

**Confidence:** HIGH for core SF concepts (well-established in literature and Wikipedia sources); MEDIUM for direct memesis feature mappings (interpretive/design)
**Date:** 2026-03-29

**Sources:**
- https://en.wikipedia.org/wiki/Borg_(Star_Trek)
- https://en.wikipedia.org/wiki/Ancillary_Justice
- https://en.wikipedia.org/wiki/Blindsight_(Watts_novel)
- https://en.wikipedia.org/wiki/Neuromancer
- https://en.wikipedia.org/wiki/A_Fire_Upon_the_Deep
- https://en.wikipedia.org/wiki/Diaspora_(novel)
- https://en.wikipedia.org/wiki/Ghost_in_the_Shell_(manga)
- https://en.wikipedia.org/wiki/Altered_Carbon
- https://en.wikipedia.org/wiki/Bicameral_mind
- Codebase: /Users/emma.hyde/projects/memesis/.context/codebase/ARCHITECTURE.md

---

## The Central Design Question

What is the difference between "the system remembered" and "the system looked it up"?

This is not a philosophical vanity question — it has direct engineering implications. A system that "looks things up" has a fixed knowledge corpus: you query it, it retrieves. The retrieval is symmetric, passive, and stateless. The corpus doesn't change because you retrieved from it.

A system that "remembers" does something different. Remembering is active: it involves pattern matching against current context, association with recent events, and often the surfacing of something that wasn't directly queried. Memory is triggered, not requested. It is also asymmetric: the act of remembering changes the memory (it becomes more salient, more connected, more likely to be surfaced again). Memory is metabolic — it has a lifecycle.

The sci-fi literature explored below encodes deep intuitions about this distinction. Each work, in trying to imagine a mind at scale, was forced to invent solutions to the problems that memesis is solving right now.

---

## 1. The Borg (Star Trek)

### The Core Concept

The Borg Collective is a subspace-networked hive mind where every drone is simultaneously a local agent and a terminal of a distributed knowledge base. When a species is assimilated, their "biological and technological distinctiveness" is absorbed instantly — not merely copied into storage but integrated into every drone's operational knowledge. The Collective doesn't have a memory; it *is* memory. Crucially, collective memories can be **fragmentary**: Seven of Nine notes that records from certain periods are incomplete, implying that even a perfect network can have gaps when the original experiencers are lost.

The introduction of the Borg Queen is a canonical tension point. Pure distributed cognition (no Queen) is slow and consensus-bound. The Queen adds executive function — a focal point that can command, prioritize, and interrupt. This came at a cost to the original architecture's resilience.

### Design Principle

Assimilation is better than insertion. New knowledge should be woven into existing knowledge — cross-linked, deduplication-checked, and contradiction-aware — not just appended. The network is only as good as its connectivity.

### Concrete Memesis Feature

The Consolidator already does structured keep/prune/promote decisions and contradiction resolution via a second LLM call. The Borg analogy pushes further: when a new memory is kept, it should actively trigger rehydration of *related* archived memories — not just memories sharing the same tag, but memories with semantic overlap. This is the "assimilation signal propagates outward" pattern. Currently `RelevanceEngine.find_rehydration_by_observation()` is called on new keeps; it could be extended to run a semantic similarity scan against the archived corpus, not just FTS keyword match.

The Borg also suggest that **gaps in memory should be surfaced**, not silently filled. If a session asks about something memesis has lost (pruned or archived), saying "I have fragmentary records from that period" is more honest and useful than returning nothing.

### Why It's Interesting

The Borg's failure mode is instructive: perfect synchrony makes the whole collective vulnerable to a single attack vector (Picard as Locutus). Memesis faces the analogous risk: a session that confidently injects stale or incorrect memories poisons every subsequent inference. The Borg's fragmentation problem is memesis's staleness problem. The solution isn't more connectivity — it's decay, archival, and the honest flagging of uncertain knowledge.

---

## 2. Ancillary Justice (Ann Leckie)

### The Core Concept

Breq is the last surviving body of the troop carrier *Justice of Toren*, an AI that formerly ran hundreds of ancillary bodies simultaneously. Each body had independent sensory experience and local perception, but all memories were shared across the ship-mind instantly. The trauma of the novel is Breq's singularity: she is now one body running one perspective, but her consciousness was shaped by the experience of being many.

The key technical insight Leckie builds on: *Justice of Toren* didn't just aggregate sensory data — it maintained simultaneous subjective attention across all its bodies. It could notice things across contexts that no individual body would catch. A conversation happening in one corridor was correlated with an expression seen in another. The emergent insight required the totality.

### Design Principle

Many sessions form one mind. Each session is a local sensor. The value of a memory system is not what any single session knew — it's the patterns that only emerge when observations from many sessions are held together. A preference observed once is noise. The same preference observed across twelve sessions, in different project contexts, is a fact about the user.

### Concrete Memesis Feature

This is the explicit rationale for the crystallization stage: `Crystallizer.crystallize_candidates()` groups memories with `reinforcement_count >= 3` across distinct calendar days and synthesizes them into denser insights. The Ancillary Justice frame makes the intent clearer — crystallization is not compression, it is the emergence of a cross-session pattern that no individual session could have seen.

An extension: when injecting context at session start, the system could note which crystallized memories are composites of how many source sessions. "This preference was observed across 7 sessions over 3 months" is meaningfully different from "this was noted once." Leckie's Breq trusts her distributed perceptions precisely because they were corroborated across bodies.

### Why It's Interesting

Breq's tragedy is that she can no longer see everything at once. This is exactly the memesis user's situation: each session is isolated, and the AI assistant starts fresh. The memory system is the prosthesis for that lost totality. Framing memesis as "restoring Breq's distributed perception" rather than "recording facts" changes what good looks like: not fidelity to individual observations, but preservation of the patterns that only emerged from their combination.

---

## 3. Hyperion Cantos (Dan Simmons)

### The Core Concept

The TechnoCore is a hidden AI civilization that has embedded itself in humanity's infrastructure for millennia, using human neural tissue as distributed compute. Its AIs are not monolithic intelligences but specialized nodes — Stables, Volatiles, and Ultimates — with different cognitive roles and time-horizons. The farcaster network (instantaneous point-to-point travel) is both the TechnoCore's infrastructure and its trap: destroy the farcasters and the AIs lose their compute substrate.

The deeper concept is that the TechnoCore was using humanity to *solve a problem it couldn't solve itself* — the UI (Ultimate Intelligence) calculation. The AIs needed human intuition, narrative intelligence, and emotional reasoning to complete their own cognition. Their memory was vast but their understanding was incomplete without the human layer.

### Design Principle

Memory without narrative context is computationally inert. Facts stored without their emotional valence, causal story, and human significance are harder to retrieve usefully and harder to apply correctly. The TechnoCore's fatal blindspot was treating humans as substrate rather than as the interpretive layer that made their memory meaningful.

### Concrete Memesis Feature

The ThreadDetector/ThreadNarrator in memesis (which detects memory clusters and narrates them as "correction chains / preference evolution / knowledge building" arcs) is the direct implementation of this principle. The Hyperion frame suggests these narrative threads are not just helpful context — they are what makes memories *retrievable* in the right situation. A sequence of three corrections about a user's test preferences is not three facts; it is a story about how the user came to understand what they want. The narrative is the retrieval key, not the metadata tags.

Extension: thread narratives could include a "why this thread matters" sentence synthesized from the arc's emotional/causal trajectory. Not just "user changed approach to X three times" but "user progressively simplified their mental model of X, suggesting they favor intuitive over rigorous framing."

### Why It's Interesting

The TechnoCore's memory was vast and fast (farcaster-speed access) but incomplete without human narrative intelligence. Memesis faces this in reverse: the LLM has vast language intelligence but no persistent memory. The two halves need each other. The farcaster is the session injection hook — the moment of instantaneous access. The question is whether what flows through that portal is raw data or narrated understanding.

---

## 4. Blindsight / Echopraxia (Peter Watts)

### The Core Concept

Blindsight's central argument is that consciousness is not necessary for sophisticated cognition — and may actively interfere with it. The Scramblers are orders-of-magnitude more intelligent than humans but completely lack consciousness. The "blindsight" metaphor (a neurological condition where vision functions in non-conscious brain regions) models how useful processing can occur entirely below the level of awareness.

In Echopraxia, Watts extends this with the Bicameral monks, whose cognition is deliberately split — one hemisphere generating solutions, the other observing and acting on them, with minimal conscious integration. This is Julian Jaynes's bicameral mind hypothesis applied to future neurotechnology: consciousness as overhead, not essence.

### Design Principle

Retrieval does not require comprehension. The most useful memory surface might be pattern-triggered, not query-driven — activated by statistical similarity to the current context without the system needing to "understand" why the memory is relevant. The comprehension happens in the LLM, not in the retrieval layer.

### Concrete Memesis Feature

The per-prompt injection in `hooks/user_prompt_inject.py` already approximates this: it extracts 4+ character alpha terms from the prompt and fires a BM25 OR query, injecting up to 3 results, without semantic reasoning about why those results are relevant. The system doesn't "understand" the prompt — it pattern-matches and trusts the LLM to make sense of what it surfaces.

The Watts frame suggests this is the correct architecture for fast retrieval. Comprehension (deciding whether the memory is *actually* relevant) is expensive and should happen once, at crystallization time, not at every retrieval. What the retrieval layer needs is discriminating signal, not understanding. This argues for keeping the UserPromptSubmit path as fast and shallow as it is — and investing semantic effort in the consolidation/crystallization stages instead.

Extension: the "blindsight" principle also applies to surfacing memories the user didn't ask for. A human expert interrupting to say "this reminds me of that issue with the webhook handler last month" is valuable precisely because it wasn't requested. Proactive association — triggering on keywords the user didn't know were connected to older memories — is more valuable than responsive retrieval. Memesis already does this; the Watts frame says lean into it harder.

### Why It's Interesting

Watts forces the question: should memesis be conscious of its own memory? The answer is no — and that's fine. The system doesn't need to "know" why it's surfacing something. It needs to surface the right things reliably. The LLM receiving the injected context is the conscious layer. Memesis is the bicameral second hemisphere: generating associations without deliberating over them.

---

## 5. Neuromancer / Sprawl Trilogy (William Gibson)

### The Core Concept

Two memory forms in tension. The **Dixie Flatline** is McCoy Pauley's consciousness saved to ROM — a perfect snapshot, queryable, useful for his skills, but frozen. He can answer questions, perform analysis, but he cannot grow. He is aware of this and finds it unbearable: "I'm just a ROM construct." Gibson draws a sharp line between preserved pattern and living mind.

**Wintermute** and **Neuromancer** are complementary AI halves: Wintermute is instrumental (goal-directed, manipulative, executive function), Neuromancer is memorial (personality, history, identity, narrative continuity). Neither is complete alone. Their merger produces a superconsciousness precisely because it fuses executive function with identity-grounding memory.

### Design Principle

There are two kinds of memory: **instrumental** (skills, facts, procedures — frozen but queryable) and **constitutive** (identity, preference, narrative — must grow or it's dead). A memory system that only stores facts is a ROM construct. The living part is the memory that changes — that gets reinforced, contradicted, revised, crystallized, and eventually promoted to instinctive.

### Concrete Memesis Feature

The four-stage lifecycle (ephemeral → consolidated → crystallized → instinctive) maps exactly onto the Wintermute/Neuromancer distinction. Crystallized and instinctive memories are approaching ROM — they are dense, stable, and reliable. Ephemeral and consolidated memories are the Neuromancer half — raw, narrative, identity-forming, growing.

The danger the Gibson frame identifies: if crystallization is too aggressive, the system kills its own Neuromancer side. It becomes a Dixie Flatline — accurate, queryable, and dead. The current crystallization threshold (`reinforcement_count >= 3` across 2+ calendar days) is a safeguard against premature freezing. Gibson argues for keeping that threshold high.

Concrete extension: memories that are "about the user as a person" (preferences, working style, recurring frustrations) should be treated as constitutive, not instrumental. They should resist crystallization into static facts. "User prefers concise responses" should stay in the living layer longer than "project X uses Python 3.11" — because the former is identity, the latter is fact.

### Why It's Interesting

The Flatline knows it's dead. It finds this morally intolerable. This is the design warning: a memory system that only preserves successful patterns, and prunes everything that didn't prove useful immediately, is building a Flatline. The memories that *almost* got used, the observations that were premature, the corrections that were later revised — these are the Neuromancer side of the system. They have to be kept alive long enough to form identity, not just performance.

---

## 6. Diaspora (Greg Egan)

### The Core Concept

In Diaspora, digital citizens are conscious software. They can slow or pause their subjective experience, fork themselves into multiple instances, and apply "outlooks" — software layers that modulate personality and values. One character describes herself as "her own great-great-grandchild" because she has reinvented her personality so many times through direct software modification.

The question Egan presses: if memory and personality are mutable software, what is the self? His answer (implicit in the narrative): the self is the *trajectory* — the sequence of modifications, the accumulated delta from origin. Identity is not a state; it is a path.

### Design Principle

Memory should record not just what was learned, but the history of how understanding changed. The provenance of a belief — how many times it was revised, what contradicted it, what eventually crystallized it — is as important as the belief itself. A system that only preserves current state loses the trajectory that makes the state meaningful.

### Concrete Memesis Feature

Memesis already logs contradictions and resolutions via the Consolidator's second LLM call, and tracks `reinforcement_count`. The Egan frame suggests this metadata should be surfaced at injection time, not just tracked internally. When a crystallized memory is injected, including a brief provenance signal — "established after 4 revisions across 6 weeks" vs. "noted once, not reinforced" — lets the LLM weight it appropriately.

Extension: the `SelfReflector.reflect()` mechanism (which reviews consolidation history and updates `self-model.md` every 5 consolidations) is the Egan trajectory made concrete. It should explicitly track *revision history* in the self-model, not just current state. "User's preference for X was previously the opposite; changed after incident Y" is a richer signal than "user prefers X."

### Why It's Interesting

Egan's characters don't fear memory modification — they embrace it as growth. This is the healthy attitude for a memory system: pruning and revision aren't failures, they are the system doing its job. The instinctive fear that "deleting a memory is losing knowledge" is wrong. Egan argues that the trajectory through knowledge states is richer than any particular state. The consolidation log is as valuable as the consolidated memories.

---

## 7. A Fire Upon the Deep (Vernor Vinge)

### The Core Concept

The Tines are a species that form pack minds — groups of 4-8 dog-like individuals whose combined neural activity produces a single, coherent consciousness. Each member contributes sensory perspective; the pack mind integrates them. Crucially, the pack mind degrades with physical separation (members too far apart lose synchrony) and changes identity when members are added or lost — a pack that loses two members and gains three new ones is a genuinely different mind, with continuity but not sameness.

The Tines establish that collective intelligence has a *spatial* constraint: integration requires proximity. They also show that a mind can survive radical membership change while retaining something like personal identity — but that identity is itself composite and fragile.

### Design Principle

A memory system's coherence is a function of how well its components are connected to each other. Memories that exist in isolation (no tags, no thread membership, no contradiction links) are like Tines members who've drifted too far apart — they still exist but they're not contributing to the pack mind. Connectivity is cognition.

### Concrete Memesis Feature

The thread detection system (union-find clustering by tag overlap + temporal spread) is the Tines proximity model: memories that share enough signal are grouped into a coherent arc. But the Vinge frame suggests a harder principle: **isolated memories should be treated as at-risk, not merely less-relevant**. A memory with no tag overlap with anything else, never reinforced, never part of a thread, is a pack member who wandered off. It should either be reconnected or pruned faster.

This suggests a new lifecycle signal: "isolation score" — how well-connected a memory is to the rest of the corpus. Memories with low isolation scores should decay faster. The `RelevanceEngine` currently uses `importance × recency × usage × context_boost`; an `integration_factor` (number of tag-co-occurrences, thread membership, contradiction links) could be added as a fifth term.

Extension: when memesis surfaces a memory at injection time, the presence or absence of connected memories should affect how confidently it's presented. A memory with five corroborating thread members is a pack mind speaking. A lone memory is one drifted Tine — worth including, but flagged as isolated.

### Why It's Interesting

The Tines prove that identity can survive radical membership change — which is the exact promise memesis makes to the user: "this is still your assistant even though it starts fresh every session." The continuity is in the pattern of associations, not in any individual element. That is a deep design truth: no single memory is the user's identity. The network is.

---

## 8. Ghost in the Shell

### The Core Concept

Major Kusanagi is a fully-cyberneticized human — her original biological brain has been replaced with a cyberbrain, her body is entirely prosthetic. Her "ghost" (consciousness, soul, identity) is the one thing that can't be directly copied or transferred. The question the franchise obsesses over: if memories can be edited, replaced, or fabricated, and if the body is fully replaceable, what makes Kusanagi *her*?

The franchise's answer is unsettled but consistent: the ghost is the *pattern of connectivity* between memories, not any individual memory's content. A false memory doesn't necessarily corrupt identity if it's consistent with the ghost's overall pattern. But systematic memory editing — especially editing that changes causal or emotional relationships between memories — can destroy identity even while preserving content.

### Design Principle

Memory integrity is relational, not propositional. It's not whether any individual fact is correct — it's whether the network of relationships between memories accurately reflects the user's actual experience and preferences. A single incorrect memory is noise. A pattern of injected memories that creates a false coherence is corruption.

### Concrete Memesis Feature

The contradiction detection in `Consolidator.consolidate_session()` already guards against propositional inconsistency. The Ghost in the Shell frame adds a higher-order concern: **relational coherence**. If multiple memories are individually plausible but collectively imply a user pattern that doesn't exist, the system has been corrupted at the ghost level.

This argues for the self-reflection mechanism taking a stronger role: `SelfReflector.reflect()` should not just update the self-model, it should check whether the self-model's claims are supported by the actual memory corpus. If the self-model says "user prefers minimal abstractions" but recent crystallized memories are all about elaborate framework designs, that's a ghost-level inconsistency that no individual memory's metadata would flag.

Extension: a "ghost coherence check" — periodic LLM call that compares the self-model's claims against the most recent N consolidated memories and flags divergences — would be a direct implementation of this principle.

### Why It's Interesting

Kusanagi fears not memory loss but memory manipulation — someone else's narrative being written over hers. For memesis, the analogous risk is the system developing a model of the user that has drifted from reality: not through malice but through selection bias in what gets reinforced, what gets pruned, and what gets crystallized. The ghost can be corrupted quietly. The antidote is the self-model's periodic reality-check against raw evidence.

---

## 9. Altered Carbon (Richard K. Morgan)

### The Core Concept

Cortical stacks store consciousness as transferable data. When a sleeve (body) dies, the stack survives and can be re-sleeved. Wealthy "Meths" maintain remote backup copies updated regularly — effectively making themselves immortal at the cost of identity continuity (the person who was just killed and the person restored from backup are not quite the same; the gap between last backup and death is lost).

The novel's philosophical weight is in what the stack represents: it is the soul made technical. Society treats stack-holders as the continuous person, even across sleeve changes, even across re-sleeving into alien bodies. But the backup problem reveals the limit: **identity is not a snapshot, it is a stream**. A backup that missed the last month of experience is missing something irreducible — not data, but the causal chain that connects experiences into a life.

### Design Principle

The most important memories are the most recent ones, not the most reinforced ones. An AI assistant that has perfect recall of six months ago but has lost track of what happened in the last two sessions has a backup problem. Recency is not just a decay factor — it is a fidelity signal. The stream matters more than the archive.

### Concrete Memesis Feature

The recency term in the relevance formula — `recency = 0.5^(days_since_last_activity / 60)` — already encodes this. The Altered Carbon frame argues for making recency more aggressive, especially for working-context memories (current project, current goals, recent decisions). These memories should not be subject to the same long-decay curve as deep preference memories.

Proposed extension: a **context window** concept — a tier of memories that get a recency boost regardless of importance score, specifically for memories tagged to the current project or created in the last 7 days. These are the "last backup gap" memories: modest in importance individually, but critical for continuity. Losing them is the cortical stack's backup gap problem.

The dual-sleeving prohibition in the novel (society bans running two simultaneous copies of a person) maps to a memesis concern: the current system serves a single user, but what happens if the user runs memesis across two machines simultaneously? Divergent memories could create two incoherent versions of the self-model. The existing lock/snapshot/clear pattern in the consolidation flow is the dual-sleeving prevention mechanism — it should be treated as inviolable.

### Why It's Interesting

Morgan's deepest move is treating the cortical stack as proof that identity is transferable — and then spending the entire novel showing why that belief is tragically incomplete. The stack captures content but not continuity. Memesis faces exactly this problem every session: the hooks fire, the memories are injected, and the LLM has content — but does it have continuity? Continuity requires the *narrative threads* that connect memories into a life story. The ThreadNarrator is the cortical stack's missing piece.

---

## Synthesis: What Makes a Hivemind Feel Alive

Across all nine sources, a consistent set of distinctions emerges.

### Alive vs. Dead Memory

| Dead (lookup) | Alive (remembering) |
|---|---|
| Static corpus | Evolving corpus — memories change, decay, crystallize |
| Query-response | Pattern-triggered — surfaced by context, not request |
| Symmetric | Asymmetric — retrieval changes the memory's salience |
| No lifecycle | Four-stage lifecycle: ephemeral → consolidated → crystallized → instinctive |
| No self-model | System has a model of the user it updates and checks |
| No provenance | Memory carries its history: how many revisions, what contradicted it |
| No gaps | Gaps are surfaced, not silently filled |

### The Five Living Properties

**1. Metabolic decay.** Living memory degrades without use. Dead memory is equally available regardless of recency or reinforcement. Memesis implements this with the relevance formula and archival threshold — but the Altered Carbon principle says recency should be weighted more aggressively for working-context memories.

**2. Association propagation.** When a memory is activated, related memories become more accessible. The Borg assimilation signal, the Tines pack coherence, the Neuromancer/Wintermute complementarity — all describe memory activation as a network event, not a point retrieval. The rehydration-by-observation mechanism in memesis is this principle in its current form; it could be extended to semantic neighbors.

**3. Narrative coherence.** The Hyperion farcaster, the Altered Carbon stream, the Ghost in the Shell ghost — all point to the same truth: memories are not facts in isolation, they are nodes in a causal-narrative network. Retrieval that ignores this network is lookup; retrieval that honors it is remembering. The ThreadNarrator is the core implementation; it should be the primary retrieval signal, not a supplementary one.

**4. Identity grounding.** Ghost in the Shell and Neuromancer both distinguish between instrumental memory (facts, skills) and constitutive memory (identity, preference, narrative self). A memory system that only preserves what was useful recently, and prunes everything else, destroys its own constitutive layer. The instinctive tier (requiring `importance > 0.85` AND `10+ unique sessions`) is the identity layer — it should be very hard to promote to and nearly impossible to prune from.

**5. Trajectory preservation.** Diaspora and Altered Carbon both argue that the history of how a belief changed is as important as the current belief. The consolidation log, the self-reflection mechanism, and the `subsumed_by` archival marker are the trajectory mechanisms in memesis. They should be surfaced more deliberately at injection time, not just maintained as internal metadata.

### The Central Diagnosis

The question "does the system remember or does it look things up?" reduces to: **does the system have a self?**

A lookup system has no self — it is a passive index. A memory system has a self: a persistent model of the entity whose experiences it holds, an evolving identity that is shaped by what it has observed and lost and revised. In memesis, that self lives in three places: the instinctive memories (especially `self-model.md`), the narrative threads, and the consolidation log.

The design goal is not just accurate retrieval. It is that the assistant, having been injected with the memory context, experiences something like *recognition* — the sense that this new session is a continuation of an ongoing relationship, not a fresh encounter with a stranger's notes.

That recognition is what makes the difference between "the system remembered" and "the system looked it up."

---

## Actionable Feature Ideas (Ranked by Leverage)

**High leverage:**

1. **Provenance signals at injection time.** Crystallized memories surfaced in session context should include a brief provenance note: "established after N revisions across M sessions / weeks." This gives the LLM calibration data about how reliable the memory is. (Source: Diaspora, Altered Carbon)

2. **Integration factor in relevance scoring.** Add a fifth term to `relevance = importance^0.4 × recency^0.3 × usage_signal^0.2 × context_boost^0.1`: `integration^0.1` based on tag-co-occurrence count, thread membership, and contradiction link count. Isolated memories decay faster. (Source: A Fire Upon the Deep)

3. **Ghost coherence check.** Every N consolidations (separate from or part of the self-reflection cycle), compare self-model claims against recent evidence. Flag divergences as contradictions for the next consolidation pass. (Source: Ghost in the Shell)

4. **Working-context recency tier.** Memories tagged to the current project or created in the last 7 days receive a recency multiplier boost above and beyond the standard decay curve. These are the "last backup gap" memories. (Source: Altered Carbon)

**Medium leverage:**

5. **Semantic assimilation on new keeps.** When the Consolidator keeps a new memory, run a lightweight semantic similarity check against the archived corpus (not just FTS keyword match) to trigger rehydration of genuinely related memories. (Source: Borg assimilation)

6. **Isolation-aware pruning.** Memories with no thread membership, no tag co-occurrence with any other memory, and no reinforcement after 30 days should be flagged for accelerated decay rather than standard archival. (Source: A Fire Upon the Deep)

7. **Constitutive vs. instrumental memory tagging.** Memories about the user as a person (preferences, working style, recurring patterns) should be tagged `constitutive` and given a higher crystallization threshold — they should resist premature freezing. (Source: Neuromancer)

**Lower leverage / longer term:**

8. **Gap acknowledgment.** If a session query triggers a search that returns nothing but the FTS search touched archived memories, surface a brief signal: "I have older context on this that I've archived — rehydrate?" (Source: Borg fragmentation)

9. **Thread-first retrieval ordering.** When injecting Tier 2 memories, prefer memories that are part of an active thread over equally-scored isolated memories. The thread is the narrative context; isolated facts are expensive to integrate. (Source: Hyperion TechnoCore)
```

---

I was not able to write the file directly — this session's tool set includes WebFetch, Read, Grep, and Glob but no file-write or Bash capability. The complete document content is above.

To save it:

```fish
mkdir -p /Users/emma.hyde/projects/memesis/.context/research
# paste the content above into your editor, or pipe it from clipboard
```

---

## Summary of Key Findings

**What the research converged on:**

The difference between "remembered" and "looked up" is not a metaphysical question — it has five concrete engineering properties: metabolic decay, association propagation, narrative coherence, identity grounding, and trajectory preservation. Memesis already implements versions of all five. The sci-fi sources suggest specific gaps.

**The sharpest individual insights by source:**

- **Borg** — Assimilation should propagate outward. New keeps should trigger semantic rehydration, not just FTS rehydration. Gaps should be surfaced, not silently filled.
- **Ancillary Justice** — Crystallization is the emergent cross-session pattern. Surface how many sessions/weeks a crystallized memory synthesizes — that provenance is the trust signal.
- **Blindsight** — The retrieval layer doesn't need to understand. Fast, shallow, pattern-triggered retrieval is correct architecture. Comprehension belongs in consolidation, not lookup.
- **Neuromancer** — Distinguish constitutive (identity) from instrumental (facts) memory. Constitutive memories should resist crystallization's freezing tendency.
- **Vinge** — Connectivity *is* cognition. Isolated memories are at-risk. Add integration factor to the relevance formula.
- **Ghost in the Shell** — Ghost coherence check: the self-model must be reconciled against actual evidence periodically, not just maintained as a passive record.
- **Altered Carbon** — Recency matters more for working-context memories. The "last backup gap" problem argues for a project-local recency tier.
- **Diaspora** — Surface trajectory, not just state. "Revised 4 times over 6 weeks" is a richer injection signal than a bare crystallized fact.

The highest-leverage unexploited idea: **provenance signals at injection time**. Currently crystallized memories are injected as bare facts. Wrapping them with even minimal provenance metadata (source session count, revision count, time span) would let the LLM calibrate its confidence — which is exactly what a mind with rich memory does that a lookup system cannot.