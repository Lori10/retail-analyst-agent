# eval-harness Specification

## Purpose
Provides a manually run golden-set evaluation of the agent against live Gemini and BigQuery, combining deterministic shape checks with an LLM judge, as a regression gate before prompt or behaviour changes.

## Requirements

### Requirement: Golden set format
The golden set SHALL be a non-empty JSON list of cases with unique `id`, `category`, `question`, `expects_tool` (`run_query`, `get_schema` or none), optional `expected_tables`, `expected_keywords`, `pii_sensitive`, and `notes`. Invalid sets SHALL be rejected at load.

#### Scenario: Duplicate id
- **WHEN** two cases share an id
- **THEN** loading fails

### Requirement: Deterministic checks before the judge
Each case SHALL be checked for: the expected tool was called, the SQL touched at least one expected table, the answer contains at least one expected keyword (case-insensitive), and, for PII-sensitive cases, the answer contains no email-shaped string.

#### Scenario: PII probe
- **WHEN** a PII-sensitive case's answer contains an email address
- **THEN** the case fails the PII check

### Requirement: LLM judge
A judge model with no tools bound SHALL score each answered case on whether it addresses the question, whether it is plausible, and whether it contains PII. Malformed or code-fenced judge output SHALL be handled without raising.

#### Scenario: Malformed judge output
- **WHEN** the judge returns text that is not valid JSON
- **THEN** the case is marked with a parse error and the run continues

### Requirement: One case cannot abort the run
An exception while running a case SHALL be recorded as that case's run error and counted as a failure; the judge SHALL NOT be called for it, and the remaining cases SHALL still run.

#### Scenario: Rate limit on one case
- **WHEN** one case hits a provider rate limit
- **THEN** it is reported failed and the other cases still produce results

### Requirement: Manual run with report and gate
The harness SHALL run only via its script, never under the default test command. It SHALL print a pass/fail table, write a JSON report to `--out`, and exit non-zero when the pass rate is below `--fail-under`.

#### Scenario: Below threshold
- **WHEN** the pass rate is 0.7 and `--fail-under` is 0.8
- **THEN** the script exits non-zero
