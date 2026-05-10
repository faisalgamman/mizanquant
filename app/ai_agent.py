"""AI Agent — LLM-powered investment analyst with template fallback.

Supports OpenAI GPT-4 and Anthropic Claude via environment variables.
Falls back to smart template-based Arabic reports when no API key is configured.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger("ai_agent")


class AIAgent:
    """AI Investment Analyst — generates Arabic market analysis and answers questions.

    Uses OpenAI GPT-4 or Anthropic Claude if available, otherwise generates
    template-based reports from smart screener data.
    """

    def __init__(self):
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._llm_available = bool(self.openai_key or self.anthropic_key)
        if self._llm_available:
            logger.info("AI Agent: LLM available (%s)",
                        "OpenAI" if self.openai_key else "Anthropic")
        else:
            logger.info("AI Agent: No LLM API key — using template-based reports")

    # ------------------------------------------------------------------
    # LLM Call
    # ------------------------------------------------------------------

    def _call_llm(self, system_prompt: str, user_prompt: str,
                  max_tokens: int = 1500, temperature: float = 0.7) -> Optional[str]:
        """Call configured LLM provider. Returns text or None."""
        if self.openai_key:
            return self._call_openai(system_prompt, user_prompt, max_tokens, temperature)
        if self.anthropic_key:
            return self._call_anthropic(system_prompt, user_prompt, max_tokens, temperature)
        return None

    def _call_openai(self, system_prompt: str, user_prompt: str,
                     max_tokens: int, temperature: float) -> Optional[str]:
        try:
            import httpx
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.warning("OpenAI API error: %s %s", resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.warning("OpenAI call failed: %s", e)
            return None

    def _call_anthropic(self, system_prompt: str, user_prompt: str,
                        max_tokens: int, temperature: float) -> Optional[str]:
        try:
            import httpx
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": temperature,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json()["content"][0]["text"]
            logger.warning("Anthropic API error: %s %s", resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.warning("Anthropic call failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Public API — Smart Scan Report (Arabic)
    # ------------------------------------------------------------------

    def generate_smart_scan_report(self, screener_data: dict) -> str:
        """Generate an AI-powered Arabic investment report.

        Uses LLM if available, otherwise generates a template-based report.
        """
        results = screener_data.get("results", [])
        total = screener_data.get("total_scanned", 0)
        halal_count = screener_data.get("halal_count", 0)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

        if self._llm_available:
            system_prompt = (
                "أنت محلل مالي إسلامي خبير. مهمتك هي تحليل بيانات الفرص الاستثمارية "
                "وكتابة تقرير استثماري باللغة العربية الفصحى. يجب أن يكون التقرير "
                "احترافياً ومفيداً للمستثمر المسلم. استخدم لغة واضحة ومنظمة."
            )
            user_prompt = (
                f"قم بتحليل بيانات المسح التالية واكتب تقريراً استثمارياً بالعربية:\n"
                f"- التاريخ: {now}\n"
                f"- إجمالي الأسهم الممسوحة: {total}\n"
                f"- الأسهم الحلال: {halal_count}\n"
                f"- عدد الفرص: {len(results)}\n\n"
                f"بيانات الفرص (أعلى {min(len(results), 8)}):\n"
            )
            for i, r in enumerate(results[:8]):
                md = r.get("momentum_details", {})
                user_prompt += (
                    f"\n{i+1}. {r.get('symbol')} - {r.get('company', '')}\n"
                    f"   النتيجة الذكية: {r.get('smart_score', 0)}/100\n"
                    f"   الأساسي: {r.get('fundamental_score', 0)}/40\n"
                    f"   الزخم الفني: {r.get('momentum_score', 0)}/30\n"
                    f"   التوقعات: {r.get('forecast_score', 0)}/30\n"
                    f"   RSI: {md.get('rsi', 'N/A')} | MACD: {md.get('macd_label', '')}\n"
                    f"   Bollinger: {md.get('bb_label', '')} | VWAP: {md.get('vwap_label', '')}\n"
                    f"   السعر: ${r.get('price', 0):.2f}\n"
                    f"   القيمة السوقية: ${r.get('market_cap', 0)/1e9:.1f}B\n"
                )
            user_prompt += (
                "\nالمطلوب:\n"
                "1. ملخص تنفيذي للفرص المتاحة\n"
                "2. تحليل أفضل 3 فرص استثمارية مع الأسباب\n"
                "3. توصية استثمارية بناءً على البيانات\n"
                "4. نصيحة للمستثمر المسلم"
            )
            llm_result = self._call_llm(system_prompt, user_prompt, max_tokens=2000)
            if llm_result:
                return llm_result

        return self._template_smart_scan_report(results, total, halal_count, now)

    def _template_smart_scan_report(self, results: list, total: int,
                                     halal_count: int, now: str) -> str:
        """Generate a template-based Arabic investment report."""
        if not results:
            return (
                f"📋 **تقرير المسح الذكي**\n"
                f"📅 {now} UTC\n\n"
                f"تم مسح {total} سهماً، ووجد {halal_count} سهماً حلالاً.\n"
                "⚠️ لا توجد فرص استثمارية تتجاوز الحد الأدنى للنتيجة حالياً."
            )

        top = results[0]
        score_label = "ممتازة" if top.get("smart_score", 0) >= 80 else \
                      "جيدة جداً" if top.get("smart_score", 0) >= 70 else "جيدة"

        lines = [
            f"📊 **تقرير المسح الذكي للفرص الاستثمارية الحلال**",
            f"📅 {now} UTC\n",
            f"إجمالي الأسهم الممسوحة: **{total}**",
            f"الأسهم الحلال: **{halal_count}**",
            f"الفرص المميزة: **{len(results)}**\n",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        for i, r in enumerate(results[:8]):
            sym = r.get("symbol", "?")
            company = (r.get("company", "") or "")[:30]
            score = r.get("smart_score", 0)
            fund = r.get("fundamental_score", 0)
            mom = r.get("momentum_score", 0)
            fcst = r.get("forecast_score", 0)
            price = r.get("price", 0)
            mcap_raw = r.get("market_cap", 0)
            mcap = f"${mcap_raw/1e9:.1f}B" if mcap_raw else "N/A"
            pe = r.get("valuation_details", {}).get("pe_val", "N/A")
            md = r.get("momentum_details", {})
            rsi = md.get("rsi", "")
            macd_label = md.get("macd_label", "")
            bb_label = md.get("bb_label", "")
            vwap_label = md.get("vwap_label", "")
            rsi_str = f"RSI {rsi}" if rsi else ""
            mom_line = f"⚡ الزخم: {rsi_str} | {macd_label[:20]} | {bb_label[:20]} | {vwap_label[:20]}"

            s_label = "ممتازة" if score >= 80 else "جيدة جداً" if score >= 70 else "جيدة"
            lines.append(
                f"\n**{i+1}. {sym}** — {company}\n"
                f"⭐ النتيجة: {score}/100 ({s_label})\n"
                f"📊 الأساسي: {fund}/40 | الزخم: {mom}/30 | التوقعات: {fcst}/30\n"
                f"💰 السعر: ${price:.2f} | القيمة السوقية: {mcap}\n"
                f"📈 P/E: {pe if isinstance(pe, str) else f'{pe:.1f}'}\n"
                f"{mom_line}"
            )

        top_mom = top.get("momentum_score", 0)
        top_fund = top.get("fundamental_score", 0)
        if top_fund >= 30 and top_mom >= 15:
            rec = "🟢 **التوصية:** فرص استثمارية ممتازة — أساسيات قوية وزخم إيجابي. يوصى بتوزيع الاستثمار على أفضل 3-5 فرص."
        elif top_fund >= 20:
            rec = "🟡 **التوصية:** أساسيات جيدة ولكن الزخم الفني متوسط. يوصى بمراقبة مؤشرات الزخم قبل الدخول."
        else:
            rec = "🔴 **التوصية:** الفرص الحالية دون المستوى المطلوب. يوصى بالانتظار لفرص أفضل."

        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━━━━",
            f"\n{rec}",
            "\n🤖 تم إعداد هذا التقرير بواسطة **المحلل الذكي** | OpenBB Forecast",
            "⚠️ هذا التحليل لأغراض معلوماتية فقط وليس نصيحة مالية.",
        ])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public API — Chat / Ask Questions (Arabic)
    # ------------------------------------------------------------------

    def ask(self, question: str, context: Optional[dict] = None) -> str:
        """Answer a user's question in Arabic using available data."""
        q = question.strip()

        if self._llm_available and context:
            system_prompt = (
                "أنت مستشار استثماري إسلامي خبير. أجب على أسئلة المستثمرين "
                "باللغة العربية الفصحى. استخدم البيانات المتاحة لتقديم إجابات "
                "دقيقة ومفيدة. إذا لم تكن متأكداً من معلومة، فقل ذلك بوضوح."
            )
            user_prompt = (
                f"السؤال: {q}\n\n"
                f"البيانات المتاحة:\n{json.dumps(context, ensure_ascii=False, default=str)[:3000]}\n\n"
                "قدم إجابة واضحة ومفيدة."
            )
            llm_result = self._call_llm(system_prompt, user_prompt, max_tokens=1000)
            if llm_result:
                return llm_result

        return self._template_answer(q, context or {})

    def _template_answer(self, question: str, context: dict) -> str:
        """Generate template-based Arabic answer for common questions."""
        q = question.lower()

        if any(w in q for w in ["أفضل سهم", "افضل سهم", "best halal", "top pick", "أفضل فرصة"]):
            screener = context.get("screener_data", {})
            results = screener.get("results", [])
            if results:
                top = results[0]
                md = top.get("momentum_details", {})
                rsi = md.get("rsi", "N/A")
                macd = md.get("macd_label", "")
                return (
                    f"🏆 **أفضل فرصة استثمارية حلال اليوم:**\n\n"
                    f"**{top.get('symbol')}** — {top.get('company', '')}\n"
                    f"⭐ النتيجة الذكية: **{top.get('smart_score', 0)}/100**\n"
                    f"📊 الأساسي: {top.get('fundamental_score', 0)}/40 | الزخم: {top.get('momentum_score', 0)}/30 | توقع: {top.get('forecast_score', 0)}/30\n"
                    f"⚡ RSI: {rsi} | MACD: {macd[:20]}\n"
                    f"💰 السعر: ${top.get('price', 0):.2f}\n"
                    f"📊 القيمة السوقية: ${top.get('market_cap', 0)/1e9:.1f}B\n"
                    f"📈 P/E: {top.get('valuation_details', {}).get('pe_val', 'N/A')}\n\n"
                    f"السبب: أساسيات قوية + زخم فني إيجابي + التوافق مع الضوابط الشرعية."
                )
            return "🔄 لم يتم إجراء مسح ذكي بعد. استخدم زر Smart Screener أولاً."

        if any(w in q for w in ["حلال", "halal", "هل سهم", "check halal"]):
            h = context.get("halal_data", {})
            if h:
                status = "حلال ✅" if h.get("is_halal") else "غير حلال ❌"
                return (
                    f"**{h.get('company_name', '')}** ({h.get('symbol', '')}): {status}\n"
                    f"نسبة الدين: {h.get('debt_ratio', 0):.1f}%\n"
                    f"نسبة الفائدة: {h.get('interest_ratio', 0):.1f}%\n"
                    f"القطاع: {h.get('sector', 'N/A')}"
                )
            return "يرجى تحديد رمز السهم للتحقق من الحلال."

        if any(w in q for w in ["سوق", "تحليل", "توصية", "market", "consensus", "forecast"]):
            consensus = context.get("consensus_data", {})
            if consensus:
                rec = consensus.get("recommendation", "N/A")
                conf = consensus.get("confidence", "N/A")
                models = consensus.get("qualified_models", 0)
                up = consensus.get("up_votes", 0)
                down = consensus.get("down_votes", 0)
                return (
                    f"📊 **تحليل الإجماع لـ {consensus.get('symbol', '')}**\n\n"
                    f"التوصية: **{rec}**\n"
                    f"الثقة: {conf}\n"
                    f"النماذج المؤهلة: {models}\n"
                    f"أصوات الشراء: {up} | أصوات البيع: {down}"
                )
            return "يرجى تشغيل التوافق (Consensus) أولاً."

        if any(w in q for w in ["السلام", "مرحبا", "hello", "hi", "مرحباً"]):
            return (
                "وعليكم السلام ورحمة الله وبركاته! 👋\n\n"
                "أنا **المحلل الذكي** لمنصة OpenBB Forecast. يمكنني مساعدتك في:\n"
                "1. 🏆 أفضل الفرص الاستثمارية الحلال\n"
                "2. 📊 تحليل الأسهم والتوقعات\n"
                "3. ✅ التحقق من الحلال\n"
                "4. 📈 إجماع النماذج والتوصيات\n\n"
                "كيف يمكنني مساعدتك اليوم؟"
            )

        return (
            f"🔍 سؤالك: \"{question[:100]}\"\n\n"
            "لم أتمكن من العثور على إجابة محددة. يمكنك تجربة:\n"
            "- 'أفضل سهم حلال اليوم'\n"
            "- 'حلل سهم AAPL'\n"
            "- 'ما هي توقعات السوق؟'\n"
            "- 'هل سهم AAPL حلال؟'\n\n"
            "💡 **نصيحة:** للحصول على تحليل أعمق، قم بتعيين مفتاح API (OpenAI أو Anthropic) "
            "في متغيرات البيئة."
        )
