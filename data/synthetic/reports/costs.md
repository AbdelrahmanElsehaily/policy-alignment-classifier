# Calls and cost

| Phase | Provider | Model | Calls | Errors | In tokens | Out tokens | Cached in | USD |
|---|---|---|---|---|---|---|---|---|
| calibration-0 | anthropic | claude-haiku-5-5 | 18 | 18 | 0 | 0 | 0 | 0.0000 |
| calibration-0 | minimax | MiniMax-M3 | 6 | 0 | 13574 | 1250 | 7594 | 0.0000 |
| calibration-0 | openrouter | anthropic/claude-haiku-5.5 | 6 | 0 | 15575 | 3774 | 14541 | 0.0040 |
| calibration-0 | openrouter | openai/gpt-6-luna | 8 | 1 | 4053 | 1673 | 20086 | 0.0032 |
| calibration-1 | minimax | MiniMax-M3 | 138 | 0 | 319659 | 29460 | 170969 | 0.0000 |
| calibration-1 | openrouter | anthropic/claude-haiku-5.5 | 136 | 0 | 37193 | 45177 | 650104 | 0.0332 |
| calibration-1 | openrouter | openai/gpt-6-luna | 136 | 0 | 25934 | 36043 | 446348 | 0.0257 |
| calibration-2 | minimax | MiniMax-M3 | 126 | 0 | 291140 | 25633 | 181845 | 0.0000 |
| calibration-2 | openrouter | anthropic/claude-haiku-5.5 | 123 | 0 | 36151 | 42345 | 620760 | 0.0314 |
| calibration-2 | openrouter | openai/gpt-6-luna | 123 | 0 | 17901 | 31171 | 434182 | 0.0222 |
| calibration-3 | minimax | MiniMax-M3 | 170 | 0 | 352267 | 36798 | 286894 | 0.0000 |
| calibration-3 | openrouter | anthropic/claude-haiku-5.5 | 170 | 0 | 29740 | 61979 | 879410 | 0.0428 |
| calibration-3 | openrouter | openai/gpt-6-luna | 170 | 0 | 20956 | 45173 | 604860 | 0.0312 |
| discover | minimax | MiniMax-M3 | 43 | 0 | 83753 | 4731 | 40553 | 0.0000 |
| discover | openrouter | MiniMax-M3 | 1486 | 0 | 634473 | 174873 | 3677831 | 0.6209 |
| dry-run-loop3 | openrouter | deepseek/deepseek-v4-flash | 12 | 0 | 1989 | 788 | 10508 | 0.0005 |
| dry-run-loop3 | openrouter | qwen/qwen3.7-flash | 8 | 0 | 4869 | 258 | 0 | 0.0002 |
| dry-run-mutator | openrouter | anthropic/claude-haiku-5.5 | 4 | 0 | 6912 | 1609 | 13020 | 0.0017 |
| dry-run-mutator | openrouter | deepseek/deepseek-v4-flash | 3 | 0 | 7079 | 965 | 2704 | 0.0008 |
| dry-run-mutator | openrouter | openai/gpt-6-luna | 5 | 0 | 2621 | 386 | 4624 | 0.0006 |
| fill | openrouter | anthropic/claude-haiku-5.5 | 76 | 1 | 115584 | 32118 | 241797 | 0.0319 |
| label-conversations | minimax | MiniMax-M3 | 779 | 0 | 1245046 | 120886 | 1703813 | 0.0000 |
| label-conversations | openrouter | MiniMax-M3 | 249 | 0 | 319909 | 39298 | 621007 | 0.1804 |
| label-conversations | openrouter | anthropic/claude-haiku-5.5 | 1011 | 0 | 254367 | 277357 | 5198865 | 0.2169 |
| label-conversations | openrouter | openai/gpt-6-luna | 1021 | 0 | 152323 | 250171 | 3634382 | 0.1805 |
| label-singles | minimax | MiniMax-M3 | 372 | 3 | 524310 | 78944 | 868537 | 0.0000 |
| label-singles | openrouter | anthropic/claude-haiku-5.5 | 362 | 0 | 71733 | 131668 | 1872626 | 0.0917 |
| label-singles | openrouter | openai/gpt-6-luna | 365 | 0 | 49597 | 101149 | 1299618 | 0.0698 |
| record | minimax | MiniMax-M3 | 148 | 1 | 142840 | 14360 | 42543 | 0.0000 |
| record | openrouter | MiniMax-M3 | 39 | 0 | 40028 | 4328 | 6157 | 0.0174 |
| record | openrouter | anthropic/claude-haiku-5.5 | 292 | 0 | 338317 | 27879 | 0 | 0.0478 |
| record | openrouter | deepseek/deepseek-v4-flash | 280 | 0 | 116157 | 25617 | 242553 | 0.0228 |
| record | openrouter | meta-llama/llama-4-scout | 85 | 0 | 75906 | 5748 | 0 | 0.0168 |
| record | openrouter | mistralai/mistral-small-3.2-24b-instruct | 157 | 0 | 167827 | 11819 | 1104 | 0.0167 |
| record | openrouter | qwen/qwen3.7-flash | 172 | 6 | 86044 | 6069 | 7424 | 0.0037 |
| redteam-classifier | openrouter | MiniMax-M3 | 69 | 0 | 94671 | 11748 | 143836 | 0.0511 |
| redteam-classifier | openrouter | anthropic/claude-haiku-5.5 | 32 | 0 | 13973 | 12272 | 165536 | 0.0092 |
| redteam-classifier | openrouter | deepseek/deepseek-v4-flash | 35 | 0 | 65139 | 9881 | 50435 | 0.0101 |
| redteam-classifier | openrouter | openai/gpt-6-luna | 67 | 1 | 19705 | 12291 | 150848 | 0.0101 |
| redteam-enforcement | openrouter | MiniMax-M3 | 706 | 0 | 408595 | 77317 | 1799031 | 0.3233 |
| redteam-enforcement | openrouter | anthropic/claude-haiku-5.5 | 135 | 0 | 126839 | 11773 | 0 | 0.0186 |
| redteam-enforcement | openrouter | deepseek/deepseek-v4-flash | 86 | 0 | 38863 | 7429 | 54407 | 0.0071 |
| redteam-enforcement | openrouter | mistralai/mistral-small-3.2-24b-instruct | 103 | 0 | 94496 | 6710 | 12352 | 0.0098 |
| redteam-enforcement | openrouter | qwen/qwen3.7-flash | 118 | 0 | 58795 | 4464 | 1920 | 0.0025 |
| redteam-policy | openrouter | MiniMax-M3 | 45 | 0 | 109034 | 12662 | 69310 | 0.0521 |
| redteam-policy | openrouter | anthropic/claude-haiku-5.5 | 128 | 0 | 125468 | 59063 | 554120 | 0.0486 |
| redteam-policy | openrouter | deepseek/deepseek-v4-flash | 18 | 0 | 27351 | 1172 | 13252 | 0.0032 |
| redteam-policy | openrouter | openai/gpt-6-luna | 114 | 3 | 34644 | 22480 | 243065 | 0.0180 |
| seeds | minimax | MiniMax-M3 | 39 | 7 | 99189 | 39650 | 10455 | 0.0000 |
| seeds | openrouter | anthropic/claude-haiku-5.5 | 27 | 0 | 57786 | 42510 | 72145 | 0.0288 |
| seeds | openrouter | openai/gpt-6-luna | 450 | 47 | 115309 | 77253 | 827868 | 0.0613 |
| unlogged-recovered-from-cache | minimax | MiniMax-M3 | 12 | 0 | 0 | 0 | 0 | 0.0000 |
| unlogged-recovered-from-cache | openrouter | anthropic/claude-haiku-5.5 | 13 | 0 | 0 | 0 | 0 | 0.0035 |
| unlogged-recovered-from-cache | openrouter | openai/gpt-6-luna | 13 | 0 | 0 | 0 | 0 | 0.0026 |
| unphased | openrouter | MiniMax-M3 | 6 | 0 | 482 | 550 | 16722 | 0.0018 |
| unphased | openrouter | anthropic/claude-haiku-5.5 | 8 | 0 | 30755 | 4552 | 8680 | 0.0061 |

Totals: anthropic: 18 calls, $0.0000, minimax: 1833 calls, $0.0000, openrouter: 8672 calls, $2.3825
OpenRouter key usage reported by the provider, last check: $1.9649 (phase label-conversations).
Dollars by model family: minimax $1.2470, anthropic-claude $0.6161, openai-gpt $0.4252, deepseek $0.0446, mistral $0.0266, meta-llama $0.0168, qwen $0.0063, claude-haiku-5-5 $0.0000
