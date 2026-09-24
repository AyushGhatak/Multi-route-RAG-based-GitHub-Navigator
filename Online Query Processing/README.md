This directory contains:
* stage0.py: This python script implements stage0, that is Domain Guardrails followed by Semantic Intent Router (Hybrid, Graph, Git routes).
* stage1_2_3.py: This python script implements stage 1, 2, 3. Where
  1. stage1: Multi-route retrieval (Hybrid, Graph, Git)
  2. stage2: Reranking, Deduplication, Dynamic Pruning, Context Compression
  3. stage3: Context Formatting, Prompt Construction, Qwen Response Generation, Citation Validation, Grounding Validation

