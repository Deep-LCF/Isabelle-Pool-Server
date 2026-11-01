# Training-metrics experiments — Ubuntu server guide (ASD-STE100 style)

## 1. Scope

NOTE: `README.md` in the same folder is the source of truth for the commands. If this guide and the README differ,
use the README.

This guide tells you how to run the training-metrics experiments on the Ubuntu server. The experiments measure
IPS as an environment for training and evaluation: rollouts per hour, throughput, latency, branch cost, agreement
with a cold build, and bounded steps. The scripts are in the folder `evaluation/training_metrics/` of the
repository. Do the steps in the order given. Do not start a step before the previous step is complete.

NOTE: Each command in this guide is one line. Type or paste one line at a time.

## 2. Requirements

Make sure that the server has these items before you start:

- The software of the memory-scaling experiments (`evaluation/memory_scaling/SERVER_GUIDE.md`, section 3): IPS,
  Isabelle2026-RC2, the HOL-Analysis heap, PIDE MCP, and the Python environment.
- About 9 hours of server time. You can stop between steps.
- No other user who runs Isabelle on the server during the experiments.
- Port 8000 is free. Each run starts its own IPS server on this port.

## 3. Prepare the shell

1. Go to the repository:
   ```bash
   cd ~/Isabelle-Pool-Server
   ```
2. Get the latest version of the branch:
   ```bash
   git pull
   ```
3. Activate the Python environment:
   ```bash
   source .venv/bin/activate
   ```
4. Put Isabelle on the path:
   ```bash
   export PATH=/opt/isabelle/bin:$PATH
   ```
   If Isabelle is in your home directory, use `$HOME/Isabelle2026-RC2/bin` in place of `/opt/isabelle/bin`.
5. Go to the experiment folder:
   ```bash
   cd evaluation/training_metrics
   ```

NOTE: A new shell (a new login, a new tmux window) does not have the Python environment. Do steps 1, 3, 4 and 5
again in each new shell.

## 4. Check the environment

1. Run the check:
   ```bash
   scripts/00_check_env.sh
   ```
2. Read the output. Each line must show PASS.
3. If a line shows FAIL, do the correction that the line gives. Then run the check again.
4. Keep the output. The report uses it as the environment description.

## 5. Prepare the corpus (seconds)

1. Make the corpus from the Isabelle that you use:
   ```bash
   scripts/00a_prepare_corpus.sh
   ```
2. Make sure that the last line shows `corpus: 94 of 94 theories` and `missing none`.

## 6. Run the census (about 20 minutes or more)

1. Start the census in the background. It writes its log:
   ```bash
   nohup scripts/01_census.sh >> results/census.log 2>&1 &
   ```
2. To see the progress, show the end of the log:
   ```bash
   tail -n 20 results/census.log
   ```
   The census shows a line for each file. It also shows a line for each block that times out or runs for 120
   seconds or more. A large file (for example `Change_Of_Vars`) can take a long time.
3. Wait until the census is complete. It replays each corpus file one time. The last line of the log shows the
   totals.
4. Make sure that `results/census/census.json` exists.

NOTE: If the census stops (you stop it, or the connection stops), start the same command again. The census
continues after the last file that it completed. It starts again from the first file if the corpus or `ips.env`
changed. If only the code changed, and the change does not touch the server or the replay, the census keeps each
file whose blocks did not change and runs only the other files. The first lines of the log tell which. To start
again from the first file, add `--fresh` after `scripts/01_census.sh`.

NOTE: If the log says `no file kept from commit …` but the change cannot change a replay (for example, it only
removes code that nothing calls), stop the census. Move `results/census/files.jsonl.prev` back to
`results/census/files.jsonl`. Then start the census again with `--keep-unchanged` after `scripts/01_census.sh`.
Do the move before you start the census again: the next start replaces `files.jsonl.prev`.

CAUTION: If the census shows "proved" below the limit (exit code 3), the census measured the wrong thing. Do not
continue. Send the output to the builder.

CAUTION: If the census shows `CENSUS SPANS FAILED` (exit code 4), the command spans of some blocks are not correct.
You can run T1 to T5. Do not run T6 (step 9.5). Send the output to the builder.

## 7. Run the smoke test (about 15 minutes)

1. Run the smoke test:
   ```bash
   scripts/02_smoke.sh
   ```
2. The script deletes `results/smoke/` and then runs: a census of three small files, T1 with one and two
   workers, the PIDE MCP row with one worker, the T6a and T6b calibration cells, and the analysis.
3. When the script stops, show its exit status:
   ```bash
   echo $?
   ```
   It must show `0`. If it shows another number, do not continue. Read the logs in `results/smoke/runner-logs/`.
4. The T1 lines and the PIDE MCP line show `ok`. The T6b calibration can show `empty`: the smoke file then has no
   proof of 2 to 40 commands. This is correct. The calculation in step 8 then uses the T6a figures.

## 8. Set the T6 cell timeouts

1. Calculate the timeouts from the smoke and the census:
   ```bash
   python -m trainmetrics.calibrate_t6 --cell results/smoke/t6a/w1/rep0 --cell-t6b results/smoke/t6b/w1/rep0 --census results/census/census.json
   ```
2. The script shows two lines: `set t6.t6a.cell_timeout_s: <value>` and `set t6.t6b.cell_timeout_s: <value>`.
3. Open `config.yaml`. Find the line `cell_timeout_s` under `t6a:` and under `t6b:`. Write the two values there.
4. Do not write a value below 7200.

## 9. Run the experiments

Start each experiment in a session that survives a disconnection. Start one experiment at a time. Each line below
starts one experiment in the background and writes its log.

1. T1 throughput and the PIDE MCP row (about 1 hour 20 minutes):
   ```bash
   nohup scripts/10_run_t1_t2.sh >> results/t1.log 2>&1 &
   ```
2. T5 bounded step (about 1 hour):
   ```bash
   nohup scripts/11_run_t5.sh >> results/t5.log 2>&1 &
   ```
3. T4 agreement (about 1 hour):
   ```bash
   nohup scripts/12_run_t4.sh >> results/t4.log 2>&1 &
   ```
4. T3 branch cost (about 1 hour):
   ```bash
   nohup scripts/13_run_t3.sh >> results/t3.log 2>&1 &
   ```
5. T6 rollout rate (about 3 hours):
   ```bash
   nohup scripts/14_run_t6.sh >> results/t6.log 2>&1 &
   ```

NOTE: The commands add to the log (`>>`). A restart keeps the log of the run that stopped.

To see the progress of an experiment, show the end of its log (example for T1):
```bash
tail -n 20 results/t1.log
```

To see if an experiment still runs:
```bash
pgrep -af trainmetrics
```

Wait until an experiment is complete before you start the next one.

NOTE: If T1 or T6 stops, start the same command again. The runner skips the cells that are complete. To run
again a cell that did not complete, add `--rerun-partial` to the command.

NOTE: If T5 stops, start the same command again. It skips a cell that is complete.

NOTE: If T4 or T3 stops, start the same command again. It runs the full cell again.

CAUTION: Do not start two runners for the same `results` folder. The runner writes `results/RUNNER_LOCK` and
refuses to start while another runner holds it.

## 10. Analyse the results

1. Run the analysis:
   ```bash
   scripts/20_analyze.sh
   ```
2. The script writes these files:
   - `results/tables/*.csv` — the tables of each experiment.
   - `results/plots/*.png` — the plots.
   - `results/REPORT_SNIPPET.md` — the paragraphs for the report, in the order of the report's questions.
3. You can run the analysis again at any time. It reads all the cells that are complete.

## 11. Hand over the results

1. Pack the results and attach them to a GitHub Release:
   ```bash
   GH_TOKEN=<token> scripts/30_release_results.sh
   ```
   The script prints the download link. To make a second release on the same day, put `TAG=<name>` before the
   command. The release goes to the repository of `git remote get-url origin`. If that is not
   `Deep-LCF/Isabelle-Pool-Server`, put `REPO=Deep-LCF/Isabelle-Pool-Server` before the command.
2. If you do not have a token, pack the results by hand:
   ```bash
   tar czf ~/training-metrics-results.tgz results/
   ```
3. Tell the builder where the archive is.

## 12. Rules that the scripts apply

- Each cell starts a new IPS server with the settings in `config.yaml` (`ips.env`). The manifest records them.
- Each corpus file gets its own IPS session with its own imports.
- The census gives each block its time budget. A block that is slower than its budget is a timeout. The tables
  show the timeouts beside each rate.
- The PIDE MCP row uses the same files, the same heap and the same proof settings as IPS. Each file also gets
  its own PIDE session, as in IPS.
- The sampler reads the memory and the CPU of the processes one time per second.
- No language model is in the loop. The rates are the server's part of a training loop.
