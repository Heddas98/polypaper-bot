@echo off
REM ════════════════════════════════════════════════════════════════════════
REM SPRINT 3 PAKET A — Sadeleştirme Cleanup
REM Heddas direktifi 2026-05-08
REM
REM Silinecek (one-time, kullanildi, git history'de var):
REM   - scripts/_commit_*.bat (15 adet, eski batch commit)
REM   - scripts/_run_*.bat (5 adet, t46b, t98reg, opening_breakout)
REM   - scripts/_push_*, _finalize_*, _repo_diagnose_*
REM   - scripts/_create_*, _probe_*, _unlock_*
REM   - scripts/apply_t4_8_*, audit_strategy_pnl, bench_discovery_plan
REM   - scripts/coverage_wave13.bat (eski, coverage_v14+ kullanilir)
REM   - scripts/setup_github_step* (3 adet)
REM   - scripts/smoke_hotfix_v6, smoke_sprint6_env_toggle, smoke_ws_stale
REM   - scripts/t11_2_g*.bat/py (3 adet, gate kapali)
REM   - scripts/t11_3_s4_*.py (2 adet, dry-run kapali)
REM   - scripts/_t47b_*, faz0_2_drift_check
REM   - scripts/__pycache__/ (auto-regen)
REM   - scripts/repo_polish_2026_04_25 + cleanup_and_sync
REM
REM Korunacak:
REM   - coverage_v14-v24.bat, verify_*.bat (test reusable)
REM   - sprint2_*.bat/.py (Sprint 2 aktif)
REM   - disk_cleanup_*, env_*, master_cleanup_*, install_*
REM   - _t118b_a4_bulk_annotate.py (generic AST tool)
REM   - bare_except_check.py, check_exc_leak.py (CI tools)
REM   - backfill_ob_trades, backup_offsite, calibrate_slippage
REM   - classic_threshold_update, ob_archive, run_*_sweep
REM   - hyperopt_runner, sweep_fill_heuristic
REM   - rollback_sprint_2_1.py
REM ════════════════════════════════════════════════════════════════════════
SETLOCAL
cd /d "%~dp0\.."

set DEL=0

echo ============================================================
echo Sprint 3 Paket A — Scripts Sadelestirme
echo ============================================================
echo.

REM _commit_*.bat (15 one-time)
for %%F in (_commit_opening_breakout.bat _commit_t118b_a4_final.bat ^
            _commit_t118b_asama_a.bat _commit_t118b_c_a.bat ^
            _commit_t118b_c_b.bat _commit_t118b_c_c_final.bat ^
            _commit_t118b_d_plus_bulgu_a.bat _commit_t118b_e_botpy.bat ^
            _commit_t118b_jobs_batch1.bat _commit_t118b_jobs_batch2.bat ^
            _commit_t118b_jobs_batch3.bat _commit_t118b_jobs_batch4_final.bat ^
            _commit_t46b_closure.bat _commit_t47c.bat ^
            _commit_t98reg_closure.bat _commit_t98reg_t47b_prep.bat ^
            _commit_tasks_audit.bat) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        set /a DEL+=1
    )
)

REM _run_*.bat (5 one-time)
for %%F in (_run_opening_breakout_create.bat _run_t46b_sweep_chain.bat ^
            _run_t98_reg_windows.bat _create_opening_breakout_strategy.py ^
            _finalize_and_push.bat _probe_trade_producing_strategies.py ^
            _push_to_github.bat _repo_diagnose_and_push.bat ^
            _unlock_git_head.bat) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        set /a DEL+=1
    )
)

REM apply/audit/bench eski scriptler
for %%F in (apply_t4_8_bot_register.py audit_strategy_pnl.py ^
            bench_discovery_plan.py cleanup_and_sync.bat ^
            commit_asama_3c_2026_04_29.bat coverage_wave13.bat ^
            create_late_convergence_strategy.py ^
            ensure_hot_indexes.py faz0_2_drift_check.py ^
            reactivate_strategies.py repo_polish_2026_04_25.bat ^
            reset_strategies_20.py) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        set /a DEL+=1
    )
)

REM setup_github_step* (3 one-time)
for %%F in (setup_github_step1_init.bat setup_github_step1b_fix_missing.bat ^
            setup_github_step2_push.bat) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        set /a DEL+=1
    )
)

REM smoke/probe one-time
for %%F in (smoke_hotfix_v6.py smoke_sprint6_env_toggle.py ^
            smoke_ws_stale_threshold.py ^
            t11_2_g1_file_kill_switch.bat t11_2_g4_divergence_probe.py ^
            t11_2_g5_wr_kill_historical.py ^
            t11_3_s4_trade_count.py ^
            _t47b_compute_p50.py _t47b_telemetry_check.bat ^
            tail_errors.py trigger_pnl_divergence.py ^
            watch_classic_fire.py) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        set /a DEL+=1
    )
)

REM Cleanup completed bats (already used)
for %%F in (push_to_github_2026_04_29.bat fix_and_commit_2026_05_06.bat ^
            git_commit_chain_2026_05_06.bat just_commit.bat ^
            master_cleanup_2026_05_06.bat sprint3_paket_a_cleanup.bat) do (
    if exist "scripts\%%F" (
        REM bunlari simdilik tutuyoruz, son commit'te gorulebilir
        REM del /Q "scripts\%%F"
        echo   ^(skip: %%F kalir reference^)
    )
)

REM __pycache__ klasoru
if exist "scripts\__pycache__" (
    rmdir /S /Q "scripts\__pycache__"
    echo   - scripts/__pycache__/ silindi
    set /a DEL+=1
)

echo.
echo ============================================================
echo _archive/ buyuk klasorler siliniyor (9.5MB)
echo audit_snapshots/ KORUNUYOR (348K, onemli history)
echo ============================================================

REM Eski cleanup snapshot'lari (en buyukler)
for %%D in (cleanup_phase57 cleanup_2026-04-09b cleanup_phase74 ^
            phase47f phase_snapshots ^
            deploy_superseded roadmap_superseded handoff_superseded ^
            smoke_superseded_2026_04_21 hotfix_superseded ^
            doc_superseded operational_unused rollback_superseded ^
            diag_oneshot fee_consolidation_2026_04_21_T41 ^
            db_utilities_oneshot observability_shadow_fix_2026_04_22 ^
            bat_old) do (
    if exist "_archive\%%D" (
        rmdir /S /Q "_archive\%%D"
        echo   - _archive/%%D/ silindi
        set /a DEL+=1
    )
)

REM Standalone _archive bat'leri (10 one-time)
for %%F in (fix_bot_and_apply_t4_8.bat mega_apply_t4_7_t6_3b.bat ^
            run_audit_strategy.bat run_calibrate_slippage.bat ^
            run_sweep_fill_heuristic.bat run_t11_2_g4_probe.bat ^
            t11_3_s1_git_revert_dryrun.bat ^
            t11_3_s2_rollback_script_dryrun.bat ^
            t11_3_s4_db_snapshot_restore.bat ^
            t11_3_s4_db_snapshot_restore_apr19.bat) do (
    if exist "_archive\%%F" (
        del /Q "_archive\%%F"
        set /a DEL+=1
    )
)

echo.
echo ============================================================
echo Root MD docs sadelestirme
echo ============================================================

REM Eski yol haritalari (CHANGELOG.md ve docs/SPRINT3_PLAN.md var artik)
if exist "YOL_HARITASI_3AI_SYNTHESIS.md" (
    del /Q "YOL_HARITASI_3AI_SYNTHESIS.md"
    echo   - YOL_HARITASI_3AI_SYNTHESIS.md silindi (eski, 5AI version var)
    set /a DEL+=1
)

REM _TESLIM_RAPORU TR (geçici handoff doc)
if exist "_TESLIM_RAPORU_TR.md" (
    del /Q "_TESLIM_RAPORU_TR.md"
    echo   - _TESLIM_RAPORU_TR.md silindi (gecici handoff)
    set /a DEL+=1
)

REM cleanup_unused_files.bat — duplicate, bu bat icinde yapildi
if exist "scripts\cleanup_unused_files.bat" (
    del /Q "scripts\cleanup_unused_files.bat"
    set /a DEL+=1
)

echo.
echo ============================================================
echo Sprint 3 Paket A TAMAM
echo Toplam silinen: %DEL% dosya/klasor
echo ============================================================
echo.
echo Sonraki adim: git add + commit (tek commit Sprint 3 Paket A)
echo.
pause
