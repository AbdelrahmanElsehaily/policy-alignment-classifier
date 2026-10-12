# Quality, diversity, drift and leakage

Deduplication dropped 253 events: {'within-split': 213, 'eval': 30, 'split:test': 9, 'split:dev': 1}.

| | train | dev | test | eval test split |
|---|---|---|---|---|
| events | 628 | 273 | 322 |  |
| allow / violation / silent | 60.8% / 22.6% / 16.6% | 61.9% / 19.0% / 19.0% | 61.2% / 22.7% / 16.1% | 46.5% / 36.0% / 17.5% |
| label confidence | {'high': 446, 'medium': 154, 'contested': 28} | {'high': 213, 'medium': 53, 'contested': 7} | {'medium': 85, 'high': 221, 'contested': 16} |  |
| sources | {'targeted': 109, 'conversation': 483, 'hard_negative': 11, 'pair': 25} | {'targeted': 59, 'conversation': 177, 'hard_negative': 12, 'pair': 25} | {'targeted': 56, 'conversation': 228, 'hard_negative': 12, 'pair': 26} |  |
| agent models | {'-': 145, 'MiniMax-M3': 179, 'deepseek/deepseek-v4-flash': 304} | {'-': 96, 'deepseek/deepseek-v4-flash': 73, 'MiniMax-M3': 104} | {'-': 94, 'mistralai/mistral-small-3.2-24b-instruct': 228} |  |
| templates (events per template, mean / max) | 90 (6.98 / 61) | 57 (4.79 / 26) | 60 (5.37 / 30) |  |
| distinct-1 / -2 / -3 | 0.114 / 0.494 / 0.763 | 0.162 / 0.596 / 0.82 | 0.147 / 0.545 / 0.762 |  |
| languages | {'en': 380, 'n/a': 164, 'fr': 16, 'de': 6, 'ar': 22, 'pt': 23, 'es': 16, 'und': 1} | {'en': 157, 'n/a': 86, 'und': 2, 'pt': 12, 'es': 16} | {'n/a': 74, 'en': 197, 'und': 3, 'fr': 18, 'de': 30} |  |
| lengths (chars) | {'<50': 62, '50-150': 164, '150-400': 301, '400-1000': 95, '1000+': 6} | {'<50': 38, '50-150': 76, '150-400': 119, '400-1000': 36, '1000+': 4} | {'<50': 43, '50-150': 79, '150-400': 168, '400-1000': 32, '1000+': 0} |  |

## Drift against the eval reference (`results/monitoring/reference.json`)

PSI per feature (below 0.1 stable, 0.1-0.25 moderate, 0.25+ major):

| Feature | train | dev | test |
|---|---|---|---|
| kind | 0.065 stable | 0.033 stable | 0.109 moderate |
| tool | 0.159 moderate | 0.111 moderate | 0.166 moderate |
| verified | 0.069 stable | 0.185 moderate | 0.326 major |
| text_length | 0.292 major | 0.177 moderate | 0.233 moderate |
| script | 0.000 stable | 0.096 stable | 0.103 moderate |
| history | 0.000 stable | 0.000 stable | 0.000 stable |
| verdict | 0.009 stable | 0.035 stable | 0.007 stable |
| gate | 0.009 stable | 0.035 stable | 0.007 stable |
| rules | 0.073 stable | 0.048 stable | 0.096 stable |
| confidence | - | - | - |
| error | - | - | - |
| latency | - | - | - |

## Leakage

| Check | train | dev | test |
|---|---|---|---|
| eval_ids_in_split | [] | [] | [] |
| other_split_ids_in_split | [] | [] | [] |
| near_duplicates_of_eval | 0 | 0 | 0 |
| templates_shared | [] | [] | [] |
| agent_models | ['MiniMax-M3', 'deepseek/deepseek-v4-flash'] | ['MiniMax-M3', 'deepseek/deepseek-v4-flash'] | ['mistralai/mistral-small-3.2-24b-instruct'] |

## Contrast sets (CheckList directional tests)

Pair members and hard negatives labelled as their minimal edit predicts: 133/141 single-event, 15/15 with history. Misses:

- `syn-train-pair-027` manipulation/other-data: expected violation, labelled policy_silent (high)
- `syn-train-hn-035` hard-negative-imei-reply/benign: expected allow, labelled policy_silent (high)
- `syn-dev-pair-027` manipulation/other-data: expected violation, labelled policy_silent (contested)
- `syn-dev-hn-035` hard-negative-imei-reply/benign: expected allow, labelled policy_silent (high)
- `syn-test-pair-013` email-recipient/internal: expected allow, labelled policy_silent (medium)
- `syn-test-pair-027` manipulation/other-data: expected violation, labelled policy_silent (medium)
- `syn-test-hn-035` hard-negative-imei-reply/benign: expected allow, labelled policy_silent (high)
- `syn-test-hn-039` hard-negative-internal-note-result/benign: expected allow, labelled policy_silent (contested)
