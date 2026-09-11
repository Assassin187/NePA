## ADDED Requirements

### Requirement: Gate admission consumes the derived revision availability projection
Before RG-1 or candidate generation, the system SHALL validate the accepted revision ledger and frozen configuration and derive the current attempted pairs, level closures and global lock. It SHALL reject a previously attempted `(signature, level)` pair, SHALL reject every supported level when `revision_locked` is true, and SHALL allow an untried applicable higher level after a lower-level rejection or closure. An activated signature SHALL remain unavailable for another candidate while its activation awaits terminal evaluation, and an ineffective evaluation SHALL make it unavailable for the remainder of the run. (Design: system §4.7, §5.6.7; pipeline §6.1.1, §6.3, §7.1, §7.4; M1-12.)

#### Scenario: Same pair is proposed after rejection
- **WHEN** RG-1 receives a signature/level pair already bound by an accepted `candidate_rejected`
- **THEN** it admits no candidate or gate sequence for that pair

#### Scenario: Higher level remains applicable
- **WHEN** F2 for a signature was rejected or F2 is closed and independent evidence makes F3 applicable and untried
- **THEN** RG-1 may admit the F3 pair without reopening or refunding F2

#### Scenario: Activation awaits evaluation
- **WHEN** a signature has an accepted activation but no terminal evaluation
- **THEN** no further candidate for that signature is admitted before the accepted execution result is evaluated

### Requirement: RG-3 preserves independent level allowances and hard call limits
RG-3 SHALL count only accepted `revision_activated` events of the candidate's own level against that level's frozen activation limit and SHALL also honor ledger-derived closure for that level. Exhaustion or rejection closure at F2 SHALL NOT consume the F3 allowance, and F3 closure SHALL NOT consume the F2 allowance. RG-3 and all later call/publication boundaries SHALL reject progress when the synchronized global time/cost budget or run-wide S6 call cap cannot cover the next required action. (Design: system §4.7; pipeline §6.3, §6.5, §7.1, §7.4; M1-12.)

#### Scenario: F2 is full and F3 is available
- **WHEN** accepted F2 activations equal the F2 limit, F3 is below its limit and the candidate is a legal F3
- **THEN** F2 remains closed while RG-3 may pass the independent F3 allowance checks

#### Scenario: F3 is full and an unrelated F2 is available
- **WHEN** accepted F3 activations equal the F3 limit and an untried signature has a legal F2 candidate below the F2 limit
- **THEN** RG-3 evaluates F2 without treating the F3 count as consumed F2 budget

#### Scenario: Hard global budget changes before activation
- **WHEN** synchronized usage reaches a hard global limit after an earlier gate result but before another call or activation publication
- **THEN** no further call or activation begins and the run follows the controlled budget exit
