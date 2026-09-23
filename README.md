# Multi-route-RAG-based-GitHub-Navigator

A graph-grounded, multi-route Retrieval-Augmented Generation (RAG) system for understanding, navigating, searching, and investigating software repositories.

The **Multi-route-RAG-based-GitHub-Navigator** is designed to answer questions about a GitHub repository using the repository's actual source code, structural relationships, documentation, and Git history.

---

# Project Overview

Traditional repository RAG systems commonly flatten source code into text chunks and perform semantic similarity search.

That approach can work for simple questions, but it loses important software-engineering structure.

For example:

> What class contains `dispatch_request()`?

This is not primarily a semantic similarity problem.

The system needs to understand:

```text
dispatch_request()
        │
        ▼
AST structure
        │
        ▼
parent relationship
        │
        ▼
containing class
```

Similarly:

> Who modified `dispatch_request()`?

is not a code-semantic question.

It requires:

```text
symbol
   │
   ▼
file / line range
   │
   ▼
Git blame / history
   │
   ▼
author + commit + timestamp
```

And:

> How does `dispatch_request()` work?

requires semantic/code retrieval.

Therefore, the project uses **multiple specialized retrieval routes**.

---

# Core Design Philosophy

The system is built around one central principle:

> **Different repository questions require different retrieval mechanisms.**

The system therefore separates retrieval into three primary routes:

| Route  | Primary purpose                                    |
| ------ | -------------------------------------------------- |
| HYBRID | Semantic and implementation understanding          |
| GRAPH  | Structural and AST relationships                   |
| GIT    | History, provenance, authorship, blame and changes |

These routes are preceded by a lightweight domain guardrail and embedding-based intent router and followed by route-specific reranking/compression and grounded answer generation.

---

# Complete End-to-End Pipeline

The complete system can be divided into two major phases.

## Offline Repository Preparation

```text
GitHub Repository
       │
       ▼
Clone Repository Locally
       │
       ▼
Scan All Files
       │
       ▼
Parse and Classify Files into Tiers
       │
       ├── Tier 1 → Tree-sitter AST Parsing
       │
       ├── Tier 2 → Text Parsing / Chunking
       │
       └── Tier 3 → Binary Metadata
       │
       ▼
Normalize / Format Parsed Content
       │
       ▼
Generate Intermediate JSON
       │
       ▼
Create Graph Entities (Nodes & Relationships)
       │
       ▼
Generate Embeddings
       │
       ▼
Neo4j Ingestion using Cypher Queries
       │
       ▼
Create Search Indexes
```

## Online Query Processing

```text
User Query
    │
    ▼
Stage 0
    │
    ├── Domain Guardrail
    │
    └── Semantic Intent Router
            │
            ├── HYBRID
            ├── GRAPH
            └── GIT
                    │
                    ▼
                 Stage 1 (Multi-route retrieval)
                    │
                    ▼
                 Stage 2 (Reranking, Deduplication, Dynamic Pruning, Context Compression)
                    │
                    ▼
                 Stage 3 (Context Formatting, Prompt Construction, Qwen Response Generation, Citation Validation, Grounding Validation)
                    │
                    ▼
              Final Answer
```

---

# Graph-based Vector Database Layer (Neo4j Aura) Preperation

Before answering queries, the repository is converted into a searchable and structurally meaningful knowledge base.

The initialization pipeline performs:

1. Repository cloning
2. File discovery
3. File classification
4. Parsing
5. AST/entity extraction
6. Chunking
7. Content normalization
8. Embedding generation
9. Neo4j graph construction
10. Search-index construction
11. Git metadata availability

This creates the persistent repository representation (vector dB) used by the retrieval pipeline.

---

## Local Repository Cloning

The system first clones the target GitHub repository locally.

The local repository is important because it provides access to both:

* current repository files
* complete Git history

The file system therefore acts as the source for:

```text
Source files
Git metadata
Commit history
Blame information
Diffs
File paths
Line ranges
```
---

## Custom Tree-sitter Code Parser

A major part of the project is a custom code parsing system built using **tree-sitter**.

Tree-sitter provides concrete syntax trees that can be used to identify structural elements in source code.

Instead of simply splitting source files every N characters, the parser attempts to understand the actual source structure.

The parser system contains:

```text
BaseLanguageHandler
        │
        ├── PythonHandler
        ├── GoHandler
        ├── CPPHandler
        ├── CHandler
        ├── JavaHandler
        ├── RustHandler
        ├── TSHandler
        ├── JSHandler
        ├── RubyHandler
        ├── CSSHandler
        ├── HTMLHandler
        ├── JSONHandler
        ├── MarkdownHandler
        └── SQLHandler
CSVHandler
```

The project uses language-specific tree-sitter grammars and custom handlers rather than relying on one generic parser.

Each handler is responsible for translating language-specific syntax-tree structures into a common internal representation.

---

## Parser Architecture

The parser has a central orchestration layer.

Conceptually:

```text
parse_codebase_file(file_path, file_content)
                  │
                  ▼
             File extension
                  │
       ┌──────────┴──────────┐
       │                     │
     .csv          Tree-sitter registry
       │                     │
       ▼                     ▼
 Flat parser              Grammar
       │                     │
       ▼                     ▼
Entity Records            Parser()
                             │
                             ▼
                         AST Tree
                             │
                             ▼
                     Language Handler
                             │
                             ▼
                     Entity Records
```

The language registry is lazily initialized. This avoids loading every grammar unnecessarily during import and keeps the parser orchestration centralized.

The parser registry supports a multi-language ecosystem including:

* Python
* Go
* C
* C++
* Java
* Rust
* JavaScript
* TypeScript
* Ruby
* CSS
* HTML
* JSON
* Markdown
* SQL

Different extensions can map to the same language handler.

For example:

```text
.cpp, .hpp, .cc, .cxx
```
are handled by the C++ parser.

Similarly:

```text
.ts, .tsx
```
are handled through the TypeScript parser pathway.

JavaScript also supports:

```text
.js, .jsx
```

**Note:** The parser architecture is extensible: adding another tree-sitter grammar primarily requires adding the grammar and implementing/registering its handler.

CSV is intentionally handled separately from AST-dependent source parsing.

The central parser detects flat-file extensions before looking up a Tree-sitter grammar.

Conceptually:

```text
.csv
 │
 ▼
CSV parser
 │
 ▼
structured flat-file representation
```

This avoids attempting to interpret CSV as programming-language syntax.

---

## Three-Tier File Processing

Every repository file is classified before ingestion.

```text
                 Repository File
                       │
                       ▼
                File Classification
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     TIER 1          TIER 2         TIER 3
      Parser          Text          Binary
```

The tiers provide a controlled fallback strategy.

---

## Tier 1 — AST Parsing

Tier 1 contains files for which a dedicated parser pathway exists.

Examples include:

```text
.py, .java, .cpp, .hpp, .cc, .cxx, .c, .h, .rs, .go, .js, .ts, .jsx, .tsx, .rb, .css, .scss, .less, .html, .json, .md, .sql, .csv
```

For source files, the system performs:

```text
Read file
   │
   ▼
Tree-sitter parser
   │
   ▼
Concrete syntax tree
   │
   ▼
Language handler
   │
   ▼
Semantic entities
```

If parsing succeeds, the extracted entities become the basis for downstream graph construction.

If AST parsing fails for a file that was classified for parser processing, the system can fall back to Tier 2 text processing.

---

## Tier 2 — Text Processing

Tier 2 is used for general textual content that does not have a dedicated AST parser pathway.

Examples include:

```text
.yaml, .yml, .ini, .conf, .sh, .bat, .env, .properties, .txt
```

as well as other files identified as textual through MIME detection.

The text pathway uses **recursive character splitting**.

The current ingestion configuration uses:

```text
Chunk size:    1500 characters
Overlap:        200 characters
```

For small text files, the complete file can remain a single document node.

For larger files:

```text
Document
   │
   ├── Chunk 0
   ├── Chunk 1
   ├── Chunk 2
   └── ...
```

The chunks retain positional information such as start and end lines.

---

## Tier 3 — Binary Processing

Binary files are not parsed as source code. Instead, the system creates a contextual metadata representation.

The resulting representation contains information such as:

```text
File name
Relative path
File size
Binary asset classification
```

This means binary assets can still exist in the repository knowledge base without attempting to embed arbitrary binary bytes as source code.

The purpose of Tier 3 is repository completeness and metadata awareness rather than semantic code retrieval.

---

## Code Entity Extraction

The custom language handlers convert AST structures into normalized entities.

Depending on the language, entities can represent things such as:

```text
CLASS, MODULE, FUNCTION, METHOD, INTERFACE, STRUCT, ENUM, TYPE_ALIAS, SCRIPT
```

The parser attempts to preserve:

* entity name
* entity type
* file path
* parent entity
* parent type
* signature
* implementation body
* documentation/docstring
* scope range

This allows the ingestion layer to preserve relationships such as:

```text
File
 │
 └── Class
      │
      ├── Method
      ├── Method
      └── Method
```
rather than reducing the entire file to a flat text document. This structural information later powers the GRAPH retrieval route.

This creates a common representation across different programming languages.

---

## Code Chunking

AST extraction and chunking are deliberately separated.

The parser first attempts to identify a logical entity.

For example:

```text
Class
Function
Method
```

If the implementation body is small enough, it remains a single code node.

The current ingestion implementation uses:

```text
CHUNK_SIZE    = 1500
CHUNK_OVERLAP = 200
```

For large entities:

```text
Function_A
   │
   ├── Function_A Part 0
   ├── Function_A Part 1
   ├── Function_A Part 2
   └── Function_A Part 3
   └── ...
```

The system preserves:

```text
chunk_index
total_chunks
is_subchunked 
```
and calculates source line ranges for each sub-chunk.

This allows retrieval to return manageable pieces without completely losing the identity of the original logical entity.

---

## Intermediate Parsed Representation

Before Neo4j ingestion, parsed entities are written into intermediate JSON representations.

A typical entity contains information conceptually similar to:

```json
{
  "id": "path/to/file.py#Class.method",
  "name": "method",
  "entity_type": "METHOD",
  "scope_range": {
    "start_line": 20,
    "start_column": 4,
    "end_line": 45,
    "end_column": 20
  },
  "signature": "def method(...)",
  "implementation_body": "...",
  "associated_docstring": "...",
  "parent_id": "path/to/file.py#Class",
  "parent_type": "CLASS",
  "chunk_metadata": {
    "chunk_index": 0,
    "total_chunks": 1,
    "is_subchunked": false
  }
}
```

This intermediate representation creates a clean boundary between **parsing** and **database ingestion**.

---

## Content Formatting During Ingestion

Formatting is an important part of the architecture. The system does not simply take raw source text and insert it into Neo4j.

During ingestion, extracted content is normalized into structured fields such as:

```text
name
entity_type
signature
implementation_body
associated_docstring
scope_range
parent information
chunk metadata
```

This gives downstream retrieval a consistent representation. The formatting also ensures that retrieval does not have to reconstruct the basic identity and structure of a code entity from raw text every time.

---

## Neo4j Knowledge Graph

The structured repository representation is stored in **Neo4j Aura**.

Neo4j's Cypher language is used to create, query, and connect graph entities.

The graph serves two purposes simultaneously:

1. Structural repository representation
2. Searchable content store

This is a key architectural decision. The graph is not used only for relationships. The nodes also retain the actual retrievable content and associated metadata.

---

## Graph Nodes

The core representation is centered around `ASTNode` entities.

An `ASTNode` can represent:

```text
Class, Function, Method, Interface, Struct, Enum, Module, Document, Document Chunk, Code Chunk, Binary Asset
```

The node can contain properties such as:

```text
id, name, entity_type, file_path, signature, implementation_body, associated_docstring, parent_id, parent_type, start_line, end_line, start_column, end_column, chunk_index, total_chunks, is_subchunked, embedding
```

This means that a retrieved graph node can provide both:

```text
WHO / WHAT / WHERE
```

and:

```text
ACTUAL CONTENT
```

---

## Graph Relationships

The graph preserves structural relationships.

Important relationships include:

```text
HAS_CHILD, HAS_CHUNK, NEXT_CODE_CHUNK, NEXT_DOCUMENT_CHUNK
```

For Logical AST relationships:

```text
Class
 │
 ├── HAS_CHILD ──> Method A
 │
 ├── HAS_CHILD ──> Method B
 │
 └── HAS_CHILD ──> Method C
```

For physical chunk sequencing:

```text
Logical Entity
      │
      └── HAS_CHUNK
              │
              ▼
           Chunk 0
              │
              ▼
      NEXT_CODE_CHUNK
              │
              ▼
           Chunk 1
```

This deliberately separates **logical AST relationships** from **physical chunk sequencing**.

---

## Cypher-Based Ingestion

The parsed representation is ingested into Neo4j using Cypher queries.

The ingestion layer is responsible for creating or updating:

```text
AST nodes
relationships
metadata
content
embeddings
indexes
```

The graph therefore becomes the central repository knowledge layer.

Conceptually:

```text
Parsed JSON
    │
    ▼
Cypher ingestion
    │
    ├── MERGE nodes
    ├── SET properties
    ├── CREATE/MERGE relationships
    ├── store content
    └── store embeddings
```

Neo4j indexes provide efficient access paths for retrieval, including semantic indexes such as full-text and vector indexes.

---

## Embeddings

The system generates embeddings for retrievable repository content.

The project uses:

```text
qwen3-embedding:0.6b
```
through Ollama for embedding generation.

The same embedding model is also used by the Stage 0 semantic intent router. This provides a common semantic representation across:

```text
Repository content
Query
Intent examples
```

Embeddings are stored alongside repository content in the graph. Neo4j supports storing embeddings as vector/list properties and querying them through vector indexes.

---

## Search Indexes

The repository knowledge layer supports both semantic and lexical retrieval.

The hybrid search pathway combines:

```text
Vector Search
+
Full-Text Search
```

The current retrieval implementation uses:

```text
code_ast_index
code_fulltext_index
```

The semantic vector search retrieves nodes based on embedding similarity. The full-text search retrieves nodes based on lexical matches. These two signals are then combined into a hybrid retrieval score.

Conceptually:

```text
Query
 │
 ├───────────────┐
 ▼               ▼
Embedding       Full Text
Search          Search
 │               │
 ▼               ▼
Vector Score    FT Score
 │               │
 └───────┬───────┘
         ▼
    Combined Score
```

The hybrid route currently gives the vector component a larger contribution than the normalized full-text component.

---

## Why the Graph Stores Both Content and Structure?

A purely vector-based system would be good at:

```text
"What does this function do?"
```

but weaker at:

```text
"What class contains this function?"
```

A purely graph-based system would be good at:

```text
"What class contains this method?"
```

but would not necessarily be sufficient for:

```text
"How does this implementation work?"
```

The project therefore combines:

```text
Semantic representation
+
Lexical representation
+
Structural representation
+
Git representation
```

into one repository knowledge layer.

---

## Git Metadata and History Ingestion

Git information is treated as a separate dimension of repository knowledge.

The local clonned repository provides access to:

```text
git log, git show, git blame, commits, authors, timestamps, file history, line history, diffs, patches
```

The Git route can resolve repository entities back to files and source locations and then query the local Git repository. This is particularly important because historical questions cannot be answered reliably from the current source snapshot alone.

---

# Offline Indexing vs Online Retrieval

The system separates expensive graph-based vector db layer (neo4j) processing from query-time processing.

## Offline

Performed when preparing the graph-based vector db layer:

```text
Clone
Parse
Chunk
Format
Embed
Ingest
Index
```

## Online

Performed for each user query:

```text
Guardrail
Route
Retrieve
Rerank
Compress
Format
Generate
Validate
```

This prevents the system from repeatedly parsing and embedding the entire repository for every question.

---

# Runtime Retrieval Pipeline

The runtime pipeline is divided into:

```text
Stage 0
Stage 1
Stage 2
Stage 3
```

Each stage has a clearly defined responsibility.

---

## Stage 0 — Domain Guardrail + Intent Router

The first step determines whether the query is relevant to the repository.

The guardrail has two possible outcomes:

```text
REPOSITORY
GENERAL
```

If the query is clearly unrelated to the repository:

```text
GENERAL
   │
   ▼
REJECT
```

If the query could reasonably require repository information:

```text
REPOSITORY
   │
   ▼
ALLOW
```

The guardrail is intentionally **recall-oriented**.

It is not responsible for deciding which retrieval route should answer the question. For ambiguous queries that could reasonably refer to the repository, the system allows them to proceed. This prevents the domain guardrail from becoming an unnecessarily strict bottleneck.

After the domain guardrail allows the query, the system performs **semantic intent classification**.

The router uses:

```text
qwen3-embedding:0.6b
```

and predefined semantic examples for:

```text
HYBRID
GRAPH
GIT
```

The query is embedded and compared against the example embeddings using cosine similarity.

Conceptually:

```text
Query Embedding
      │
      ├── similarity → HYBRID examples
      │
      ├── similarity → GRAPH examples
      │
      └── similarity → GIT examples
                         │
                         ▼
                  Highest similarity
                         │
                         ▼
                    Selected route
```

The router does not treat cosine similarity as a probability. Instead, it uses the relative semantic similarity between the query and the route examples.

The resulting metadata includes:

```text
route
similarity
intent_scores
```

---

## Stage 1 — Multi-route Retrieval 

Stage 1 executes the route selected by Stage 0.

```text
Stage 0 Route
     │
     ├── HYBRID → hybrid_search_route()
     │
     ├── GRAPH  → graph_dependency_route()
     │
     └── GIT    → git_route()
```

The output of Stage 1 is **raw retrieval context**. Stage 1 does not attempt to generate the final answer.

---

### HYBRID Route

The HYBRID route is used for semantic and implementation-understanding questions.

Typical questions include:

```text
How does this function work?
Explain the implementation of this component.
What happens when this operation executes?
Why does this code behave this way?
Explain the workflow of this functionality.
```

The route combines:

```text
Vector retrieval + Full-text retrieval
```

against the repository knowledge graph.

---

#### HYBRID Retrieval

The query is converted into an embedding. At the same time, the textual query is sanitized for full-text retrieval.

Neo4j is then queried through Cypher for:

1. Vector similarity
2. Full-text matches

The results are merged and scored using **Reciprocal Rank Fusion (RRF)**.

The current route effectively uses:

```text
Semantic relevance + Lexical relevance
```

rather than depending on either signal alone.

This is especially useful when a query contains:

* natural-language concepts
* exact function names
* class names
* domain terminology
* implementation-specific vocabulary

---

### GRAPH Route

The GRAPH route is responsible for structural repository questions.

Typical examples:

```text
What class contains dispatch_request?
Which class owns this method?
Where is this function defined?
What methods are defined in this class?
What is the parent class?
Which classes inherit from this class?
What are the child classes?
```

These questions are answered using the graph structure created during ingestion.

---

#### GRAPH Retrieval Strategy

The graph route uses a structured lookup strategy. It first attempts exact AST-node resolution. The system can identify a node using:

```text
node.name
node.id
symbol-qualified identifiers
```

It then retrieves structural context such as:

```text
Parent
Children
Chunks
Next chunks
File path
Signature
Body
Documentation
Location
```

The resulting context is therefore not merely the matched node. It contains the surrounding structural information necessary to answer the question.

---

#### GRAPH Parent Resolution

For a target such as:

```text
dispatch_request
```

the graph can perform:

```text
dispatch_request
       │
       ▲
       │ HAS_CHILD
       │
   Parent Class
```

The result can include:

```text
target element
target type
file path
parent scope
parent type
parent body
parent documentation
children
physical chunks
next chunks
source location
```

This allows the Stage 2 reranker to construct a structurally meaningful answer context.

---

#### GRAPH Fallback Strategy

The graph route contains multiple retrieval levels.

Conceptually:

```text
Level 1
Exact AST node
       │
       ▼
Level 2
File-scope structural search
       │
       ▼
Level 3
Semantic graph fallback
```

This prevents an exact-symbol miss from immediately producing an empty retrieval result.

---

### GIT Route

The GIT route is responsible for repository history and provenance.

Typical questions include:

```text
Who modified this code?
Who authored this file?
When was this functionality introduced?
Show the history of this function.
What commits changed this functionality?
Show the diff for this commit.
Who last modified these lines?
```

The Git route works against the locally cloned repository.

---

#### GIT Query Metadata Extraction

Before executing Git operations, the query is analyzed to extract relevant metadata using
```text
qwen2.5-coder:1.5b
```

Potential metadata includes:

```text
symbols
file_paths
line_ranges
commit_hashes
```

This is important because Git operations are fundamentally target-dependent.

For example:

```text
Who modified lines 120-140?
```

requires:

```text
file + line range
```

while:

```text
Show the history of file1.py.
```

requires:

```text
file path
```

and:

```text
Show the patch for commit abc123.
```

requires:

```text
commit hash
```

---

#### GIT Target Resolution

The Git route can resolve repository symbols and paths through the graph when necessary.

This creates a bridge:

```text
User Query
   │
   ▼
Query Metadata
   │
   ▼
AST / Graph Resolution
   │
   ▼
Repository File / Symbol / Lines
   │
   ▼
Git Operation
```

This allows the Git route to work with both explicit and repository-derived targets.

---

#### GIT History Retrieval

The Git route supports multiple forms of historical retrieval.

1. File history

```text
git log
```

can retrieve the history of a specific file.

2. Symbol history

A symbol can first be resolved to its containing file and source location, after which relevant Git history can be retrieved.

3. Line history

Line ranges can be associated with blame/history operations.

4. Blame

The system can retrieve authorship information for relevant source lines.

5. Commit information

A commit can be resolved to:

```text
commit hash
author
date
message
```

6. Commit diff

Commit patches can be retrieved using Git operations such as:

```text
git show --stat -p
```

This provides both metadata and actual code changes.

---

### Stage 1 Output

HYBRID and GRAPH retrieval generally produce:

```text
CODE context
```

GIT retrieval produces:

```text
GIT context
```

The route metadata is preserved throughout the pipeline. This allows later stages to know not only what was retrieved, but also why it was retrieved.

---

## Stage 2 — Reranking and Contextual Compression

Stage 1 intentionally retrieves more information than is ultimately needed. Stage 2 converts the raw retrieval set into a compact, high-value context.

The Stage 2 pipeline includes:

```text
Raw Results
    │
    ▼
Normalization
    │
    ▼
Deduplication
    │
    ▼
Relevance Reranking
    │
    ▼
Dynamic Pruning
    │
    ▼
Token Budget Control
    │
    ▼
Context Compression
```

This prevents the final LLM from receiving large amounts of redundant repository content.

---

### Reranking

Initial retrieval ranking is not always sufficient. A result can have high semantic similarity while being structurally less useful. Stage 2 therefore performs additional relevance processing using 
```text
BAAI/bge-reranker-v2-m3
```
The project uses reranking to improve the ordering of retrieved candidates before final context construction.

The purpose is:

```text
Retrieval relevance + Context usefulness + Query-specific relevance
```

rather than simply taking the top raw search results.

---

### Deduplication

Different retrieval mechanisms can return overlapping information.

For example:

```text
Vector Search
      │
      ├── Function A
      │
Full Text
      │
      └── Function A
```

Without deduplication, the same implementation could occupy multiple context slots. Stage 2 removes redundant candidates before compression.

---

### Dynamic Pruning

Not every retrieved result deserves to reach the final prompt. Stage 2 dynamically prunes low-value context based on relevance and available token budget.

This prevents:

```text
large retrieval set
        ↓
large prompt
        ↓
irrelevant information
```

and instead produces:

```text
large retrieval set
        ↓
reranking
        ↓
pruning
        ↓
small high-value context for the prompt
```

---

### Code Context

HYBRID and GRAPH results are transformed into a common `CODE` context representation.

This can contain:

```text
Target entity
File path
Entity type
Signature
Implementation body
Docstring
Parent information
Child information
Chunks
Source location
```

The final code context is then formatted for Stage 3.

---

### Git Context

Git context is intentionally different from code context.

Git retrieval can produce three distinct information categories:

```text
blame_block
history_log
commit_diff
```

These are preserved separately during Stage 2 rather than flattening them immediately.

Conceptually:

```text
                 GIT RESULTS
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       BLAME        HISTORY       DIFF
          │           │           │
          └───────────┼───────────┘
                      ▼
              compressed GIT context
```

This is important because:

* blame answers **who**
* history answers **when/what changed**
* diff answers **what the actual code change was**

---

### Git Context Token Budgets

The Git compression stage maintains separate budgets for different Git information categories.

The architecture preserves separate limits for:

```text
blame information
history / commit log
commit diff
```

This prevents a large patch from consuming the entire context budget and leaving no room for authorship or history information.

---

### Membership Checking

Stage 2 also performs **membership checking** using 
```text 
qwen2.5-coder:1.5b
```
to verify that retrieved results actually belong to the repository scope requested by the user.

This is especially important for queries involving specific files, folders, symbols, or repository entities. Retrieved candidates are checked against the query's explicit membership requirements before they are included in the final context.

---

## Stage 3 — Grounded Generation

Stage 3 is the final generation stage.

It receives:

```text
User Query + Compressed Retrieved Context + Route Metadata + Context Type
```

and constructs the appropriate prompt.

The system uses a Qwen code-oriented model through Ollama:

```text
qwen2.5-coder:7b-instruct-q4_K_M
```

with Ollama running locally.

---

### Route-Specific Prompt Construction

Stage 3 does not use exactly the same prompt for every route.

The context type determines the generation strategy.

For code:

```text
CODE context
   │
   ▼
Code-oriented grounded prompt
   │
   ▼
Qwen
```

For Git:

```text
GIT context
   │
   ▼
History/provenance-oriented grounded prompt
   │
   ▼
Qwen
```

This ensures that the final model understands whether it is reasoning about:

```text
current source code
```

or:

```text
repository history
```

---

### Final Context Formatting

Formatting occurs at two important points in the architecture.

* Formatting During Ingestion

The raw repository is converted into normalized entities:

```text
raw source
   ↓
parsed entity
   ↓
structured fields
   ↓
Neo4j
```

This provides consistent storage.

* Formatting Before Generation

Retrieved results are then converted into a final LLM-readable context:

```text
Neo4j / Git retrieval
        ↓
raw retrieval
        ↓
Stage 2 compression
        ↓
formatted context
        ↓
Stage 3 prompt
        ↓
Qwen
```

This second formatting stage is important because the database representation is optimized for retrieval, while the final prompt is optimized for reasoning.

The system therefore deliberately does **not** treat stored content and prompt context as the same representation.

---

### Citation Validation

The final answer is not accepted solely because the LLM generated text. The system validates the answer against the retrieved evidence.

Source information can include:

```text
file paths
symbols
line ranges
commit identifiers
Git metadata
retrieved context
```

The system validates citations and ensures that generated references correspond to available retrieved evidence.

---

### Grounding Validation

After generation, the system evaluates whether the answer is grounded in the retrieved context.

Conceptually:

```text
Generated Answer
       │
       ▼
Grounding Validator
       │
       ├── Grounded
       │
       └── Not Grounded
```

Only grounded answers are allowed through.

---

### Fail-Closed Answer Generation

A particularly important design choice is that the system **fails closed**. If sufficient retrieved evidence is not available, Stage 3 does not attempt to guess. Instead, it returns:

```text
Insufficient retrieved context to determine this.
```

This prevents the LLM from filling missing repository information with general model knowledge. The intended behavior is:

```text
No evidence
    │
    ▼
No fabricated answer
```

rather than:

```text
No evidence
    │
    ▼
LLM guesses
```

This is critical for repository-grounded code intelligence.

---

# Complete Query Flow

Consider:

> What class contains `dispatch_request()`?

The complete pipeline is:

```text
User Query
    │
    ▼
Domain Guardrail
    │
    ▼
REPOSITORY
    │
    ▼
Embedding Intent Router
    │
    ▼
GRAPH
    │
    ▼
Stage 1
graph_dependency_route()
    │
    ▼
Exact AST node lookup
    │
    ▼
Parent lookup
    │
    ▼
Class context
    │
    ▼
Stage 2
    │
    ├── deduplicate
    ├── rerank
    ├── prune
    └── compress
    │
    ▼
CODE context
    │
    ▼
Stage 3
    │
    ├── format context
    ├── construct grounded prompt
    ├── Qwen generation
    ├── citation validation
    └── grounding validation
    │
    ▼
Final Answer
```

---

# Example Query Types

## Semantic / Implementation

```text
How does dispatch_request() work?
Explain how this component processes the input.
Why does this function behave this way?
What happens when this operation is executed?
```

Expected route:

```text
HYBRID
```

---

## Structural

```text
What class contains dispatch_request()?
Which class owns this method?
What methods are defined in MethodView?
Which class does this class inherit from?
```

Expected route:

```text
GRAPH
```

---

## Git History

```text
Who modified dispatch_request()?
When was this functionality introduced?
What commits changed this file?
Show the history of this function.
Who last modified these lines?
Show the diff for this commit.
```

Expected route:

```text
GIT
```

---

# Design Decisions

## 1. Repository-aware rather than generic RAG

The system is designed specifically for software repositories.

It therefore models:

```text
code
structure
files
symbols
relationships
chunks
Git history
```

rather than treating the repository as ordinary documents.

## 2. Specialized retrieval routes

A single retrieval method cannot optimally answer every repository question.

Therefore:

```text
Semantic question → HYBRID
Structural question → GRAPH
Historical question → GIT
```

## 3. AST-aware retrieval

The system does not rely entirely on arbitrary character chunks. Logical code entities are identified first.

This allows retrieval to understand:

```text
class
function
method
parent
child
scope
source location
```

## 4. Graph + Vector + Full Text

Each retrieval mechanism solves a different problem.

### Graph

Good for:

```text
relationships
containment
hierarchy
ownership
structure
```

### Vector search

Good for:

```text
semantic similarity
conceptual questions
implementation understanding
```

### Full-text search

Good for:

```text
exact names
symbols
keywords
lexical matches
```

### Git

Good for:

```text
history
provenance
authorship
blame
diffs
commits
```

The project combines all four.

---

# Technology Stack

The project combines:

| Component             | Technology                         |
| --------------------- | ---------------------------------- |
| Repository source     | Git / GitHub repositories          |
| Local repository      | Git clone                          |
| Parsing               | tree-sitter                        |
| Parser implementation | Python                             |
| Graph database        | Neo4j aura                              |
| Graph query language  | Cypher Query Language                          |
| Embeddings            | `qwen3-embedding:0.6b`             |
| Local LLM runtime     | Ollama                             |
| Generation model      | `qwen2.5-coder:7b-instruct-q4_K_M` |
| Semantic retrieval    | Neo4j vector search                |
| Lexical retrieval     | Neo4j full-text search             |
| Graph retrieval       | Cypher                             |
| Git retrieval         | Local Git commands                 |
| Reranking             | BAAI/bge-reranker-v2-m3         |
| Context compression   | Stage 2 contextual compression     |
| Final generation      | Qwen via Ollama                    |

Neo4j vector indexes are designed specifically to retrieve nodes or relationships based on similarity between stored embeddings and a query embedding.

---

# Project Structure

A conceptual project structure is:

```text
Multi-route-GitHub-Navigator/
│
├── codeparser/
│   ├── base_handler.py
│   ├── python_handler.py
│   ├── javascript_handler.py
│   ├── typescript_handler.py
│   ├── java_handler.py
│   ├── go_handler.py
│   ├── cpp_handler.py
│   ├── c_handler.py
│   ├── csharp_handler.py
│   ├── rust_handler.py
│   ├── ruby_handler.py
│   ├── css_handler.py
│   ├── html_handler.py
│   ├── json_handler.py
│   ├── markdown_handler.py
│   ├── sql_handler.py
│   └── orchestrator.py
│
├── ingestion/
│   ├── repository_clone.py
│   ├── file_classifier.py
│   ├── parser_pipeline.py
│   ├── chunking.py
│   ├── formatter.py
│   ├── embedding.py
│   └── neo4j_ingestion.py
│
├── retrieval/
│   ├── hybrid_route.py
│   ├── graph_route.py
│   └── git_route.py
│
├── pipeline/
│   ├── stage0.py
│   ├── stage1.py
│   ├── stage2.py
│   └── stage3.py
│
└── README.md
```

---

# Why this architecture is different?

The system is not simply:

```text
Question
   ↓
Vector Search
   ↓
LLM
```

Instead, it is:

```text
                 Repository
                     │
          ┌──────────┴──────────┐
          │                     │
      Source Code            Git History
          │                     │
          ▼                     ▼
    Tree-sitter              Git Metadata
          │                     │
          ▼                     │
     AST Entities               │
          │                     │
          └──────────┬──────────┘
                     ▼
                  Neo4j
                     │
        ┌────────────┼────────────┐
        │            │            │
     Vector       Full Text     Graph
        │            │            │
        └────────────┼────────────┘
                     │
             Stage 1 Retrieval
                     │
                     ▼
             Stage 2 Reranking
                     │
                     ▼
          Stage 2 Context Compression
                     │
                     ▼
             Stage 3 Generation
                     │
                     ▼
         Stage 3 Grounding Validation
                     │
                     ▼
                Answer
```

The repository is therefore represented at multiple levels:

```text
Physical level
    → files and paths

Syntactic level
    → AST structures

Semantic level
    → embeddings

Structural level
    → graph relationships

Historical level
    → Git commits, blame and diffs
```

These representations complement each other rather than competing with each other.

---

# Future Extensions

The architecture is intentionally extensible.

Potential future additions can be implemented without replacing the existing pipeline.

Possible extensions include:

```text
Additional tree-sitter languages
Additional repository metadata
More graph relationships
Import/dependency graphs
Cross-file symbol resolution
Call graphs
Advanced Git ancestry analysis
More retrieval strategies
Improved reranking
Repository-level summarization
Multi-repository search
```

The existing architecture provides a foundation for these because parsing, ingestion, retrieval, reranking, and generation are separated.

---

# Conclusion

The **Multi-route-RAG-based-GitHub-Navigator** is a repository-specific RAG architecture that combines program analysis, graph databases, semantic retrieval, lexical retrieval, and Git history analysis.

Its central design is the separation of responsibilities:

```text
Tree-sitter
    → understand source structure

Tiered ingestion
    → handle different repository file types

Neo4j
    → store repository structure + content + embeddings

HYBRID
    → understand implementation and behavior

GRAPH
    → understand structural relationships

GIT
    → understand historical changes and provenance

Stage 2
    → select, rerank and compress useful evidence

Stage 3
    → generate a grounded answer

Validation
    → prevent unsupported answers
```


That distinction is the foundation of the project.

---

# Disclaimer

This project is intended for **educational, research, and experimental purposes**.

The system uses automated code parsing, graph-based retrieval, vector search, reranking, Git history analysis, and large language models to generate answers about software repositories. Generated responses may contain incomplete, inaccurate, or outdated interpretations of the underlying code or repository history.

The outputs should therefore be treated as **assistance for codebase exploration and research**, rather than as authoritative documentation or a substitute for manually inspecting the source code, Git history, or official project documentation.

---

# Author

### Ayush Ghatak

• Generative AI • Large Language Models (LLMs) • Machine Learning • Deep Learning • Natural Language Processing (NLP) • Code Intelligence • Retrieval-Augmented Generation (RAG) • Graph-Based Retrieval • Vector Search • Semantic Search • Cross-Encoder Reranking • Knowledge Graphs • Neo4j • Tree-sitter • Python • Git Analysis • Repository Intelligence • Prompt Engineering • Ollama • Qwen • Sentence Transformers • Embeddings • Information Retrieval

