# Real synthetic DeepSeek regression responses

CAND-01-native-evidence.json and CAND-06-native-evidence.json are actual model responses captured on 21 October 2026 using the fictional profiles in qa/four_cases.json and model deepseek-v4.1-flash through the configured provider. They contain no API credentials or real applicants.

Both reproduced invalid_model_json because native numeric, boolean and list quotes were returned where the public evidence.reason contract requires strings. CAND-06 also quoted empty fields. Tests verify compatibility normalization against the original source values while preserving strict rejection of unproven quotes.
