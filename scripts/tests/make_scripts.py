"""Write test scripts with the local LLM (llama-server on 8105)."""
import json
import httpx

URL = "http://127.0.0.1:8105/v1/chat/completions"


def ask(prompt, max_tokens=4000, schema=None):
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0.8,
            "chat_template_kwargs": {"enable_thinking": False}, "reasoning_budget": 0}
    if schema:
        body["response_format"] = {"type": "json_object", "schema": schema}
    return httpx.post(URL, json=body, timeout=900).json()["choices"][0]["message"]["content"]


mono = ask("Write a spoken monologue of about 300 words for a talking-head video. A woman explains, warmly and "
           "clearly, why taking short walks after lunch improves focus in the afternoon. Natural spoken English, "
           "short sentences, no headings, no stage directions, no emojis. Output only the spoken text.", 900)
schema = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "object", "properties": {
    "speaker": {"type": "string", "enum": ["A", "B"]}, "text": {"type": "string"}}, "required": ["speaker", "text"]}}},
    "required": ["lines"]}
pod = json.loads(ask(
    "Write a natural two-person podcast conversation of about 1100 words in English. Speaker A is the host (a "
    "woman), speaker B is a guest (a man) who studies habits and productivity. Include small talk and some slow "
    "passages, but also 3 or 4 genuinely strong, quotable, standalone moments (a surprising statistic, a personal "
    "story with a punchline, a counter-intuitive tip). Lines of 1-4 sentences. No stage directions, no emojis. "
    'Return JSON {"lines": [{"speaker": "A"|"B", "text": "..."}]}.', 4000, schema))
open(r"C:\LocalAI\temp\test_monologue.txt", "w", encoding="utf-8").write(mono.strip())
json.dump(pod, open(r"C:\LocalAI\temp\test_podcast.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(mono.split()), "words monologue;", len(pod["lines"]), "podcast lines,",
      sum(len(l["text"].split()) for l in pod["lines"]), "words")
