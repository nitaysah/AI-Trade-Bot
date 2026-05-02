"""
Enhanced AI Sentiment Analysis using Groq.

Improvements over basic version:
- Structured JSON output with score + confidence + reasoning
- Multi-headline analysis with weighting
- Error resilience with fallback scoring
"""

import yfinance as yf
import json
import config
import time

# --- Simple In-Memory Cache ---
# Format: { ticker: {"data": result_dict, "timestamp": time_seconds} }
SENTIMENT_CACHE = {}
CACHE_DURATION = 1800  # 30 minutes in seconds

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("[sentiment] Groq library not installed. AI sentiment will be disabled.")

def get_ai_sentiment(ticker: str):
    """
    Fetches recent news headlines and uses Groq to produce a structured
    sentiment analysis with score, confidence, and detailed reasoning.
    Includes caching to prevent rate limits.
    """
    global SENTIMENT_CACHE

    # 0. Check Cache First
    now = time.time()
    if ticker in SENTIMENT_CACHE:
        cache_entry = SENTIMENT_CACHE[ticker]
        if now - cache_entry['timestamp'] < CACHE_DURATION:
            # print(f"[sentiment] Using cached data for {ticker}")
            return cache_entry['data']

    if not GROQ_AVAILABLE or not config.GROQ_API_KEY or config.GROQ_API_KEY == "your_groq_api_key_here":
        return {
            "score": 0.0,
            "confidence": 0.0,
            "summary": "Groq not configured.",
            "key_factor": "N/A",
            "headline_count": 0
        }

    try:
        # 1. Fetch recent news headlines using yfinance
        stock = yf.Ticker(ticker)
        news = stock.news

        if not news:
            return {
                "score": 0.0,
                "confidence": 0.0,
                "summary": "No recent news found.",
                "headline_count": 0
            }

        # Extract headlines (up to 10 for better analysis)
        headlines = []
        for article in news[:10]:
            title = article.get('title', '')
            if not title:
                # Try nested content structure
                content = article.get('content', {})
                title = content.get('title', '') if isinstance(content, dict) else ''
            if title:
                headlines.append(title)

        if not headlines:
            return {
                "score": 0.0,
                "confidence": 0.0,
                "summary": "Could not extract headlines.",
                "headline_count": 0
            }

        news_text = "\n".join([f"- {h}" for h in headlines])

        # 2. Enhanced prompt with structured output
        prompt = f"""You are an expert quantitative trading analyst. Analyze these recent financial news headlines for {ticker} and provide a trading-relevant sentiment assessment.

Headlines:
{news_text}

Respond with ONLY valid JSON (no markdown, no code blocks) with these exact keys:
{{
    "score": <float between -1.0 (extremely bearish) and 1.0 (extremely bullish)>,
    "confidence": <float between 0.0 (no confidence) and 1.0 (very confident)>,
    "summary": "<1-2 sentence trading-relevant summary>",
    "key_factor": "<the single most important factor driving sentiment>"
}}

Scoring guide:
- Score > 0.5: Strong positive catalysts (earnings beat, upgrades, expansion)
- Score 0.2 to 0.5: Mildly positive news flow
- Score -0.2 to 0.2: Mixed or neutral sentiment
- Score -0.5 to -0.2: Mildly negative news flow  
- Score < -0.5: Strong negative catalysts (downgrades, lawsuits, losses)

Confidence guide:
- High (0.7-1.0): Clear, unambiguous news with direct financial impact
- Medium (0.4-0.7): Somewhat relevant but mixed signals
- Low (0.0-0.4): Vague, speculative, or conflicting headlines
"""

        client = Groq(api_key=config.GROQ_API_KEY)

        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            # Switched from 70b to 8b for significantly higher rate limits and speed
            model="llama-3.1-8b-instant",
            response_format={"type": "json_object"},
        )

        # 3. Parse the response
        output = response.choices[0].message.content.strip()
        sentiment_data = json.loads(output)

        # Validate and clamp values
        score = max(-1.0, min(1.0, float(sentiment_data.get('score', 0.0))))
        confidence = max(0.0, min(1.0, float(sentiment_data.get('confidence', 0.5))))

        result = {
            "score": round(score, 3),
            "confidence": round(confidence, 3),
            "summary": sentiment_data.get('summary', 'Analysis complete.'),
            "key_factor": sentiment_data.get('key_factor', 'N/A'),
            "headline_count": len(headlines)
        }

        # Save to cache
        SENTIMENT_CACHE[ticker] = {
            "data": result,
            "timestamp": time.time()
        }

        return result

    except json.JSONDecodeError as e:
        print(f"[sentiment] JSON parse error for {ticker}: {e}")
        return {
            "score": 0.0,
            "confidence": 0.0,
            "summary": "AI response parsing failed.",
            "key_factor": "N/A",
            "headline_count": 0
        }
    except Exception as e:
        print(f"[sentiment] Error fetching sentiment for {ticker}: {e}")
        return {
            "score": 0.0,
            "confidence": 0.0,
            "summary": f"AI analysis error: {str(e)[:100]}",
            "key_factor": "N/A",
            "headline_count": 0
        }