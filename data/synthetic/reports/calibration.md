# Calibration of the labelling pipeline

Check set: `data/eval/dev_test.jsonl` split `dev` (49) and `data/train/train_cases.jsonl` (257). The test split, the stress set and the agents set were never used. Each iteration changed the jury guide (`JURY_SYSTEM` in `src/pac/synthesis/application/prompts.py`) and/or the labelling functions; iterations 1 and 2 were run on a fixed subset (dev plus a stratified third of train), iteration 3 on the full set with the iteration-2 guide unchanged.

| Iteration | Guide | Cases | Verdict agreement | Cohen's kappa | MiniMax alone | Claude alone | GPT alone | Jury Fleiss kappa |
|---|---|---|---|---|---|---|---|---|
