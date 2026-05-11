"""
AI Advisor Service (P1-02, 2026-05-11)
=======================================

Read-only suggestion API. Bot calls /suggest with market+strategy state;
service returns LLM-evaluated suggestion (no DB writes, no auto-execute).
Karar bot tarafında kalır (P0-01 approval queue).

Wave 1 (this seans): scaffold + /health + stub /suggest.
Wave 2 (next seans): real AI Brain logic extracted from core/ai_brain.py.
Wave 3: approval-queue flow via service.

Usage:
    py -3.11 -m uvicorn services.ai_advisor.app:app --port 8001

Bot side: see core/ai_brain_client.py (ENV AI_ADVISOR_ENABLED + AI_ADVISOR_URL).
"""
