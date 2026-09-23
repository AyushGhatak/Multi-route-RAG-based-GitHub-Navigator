
# !pip install GitPython --quiet

# To mount drive
#from google.colab import drive
#drive.mount('/content/drive')

"""Cloning the Github Repo in drive (locally)"""

import os
from git import Repo

# Configuration for your repository clone
LOCAL_REPO_PATH = "/content/drive/My Drive/Personal_Project/target_repo" # stored locally in drive

def clone_or_load_repo(repo_url: str) -> Repo:
    """Clones the repository if not present, otherwise loads the existing clone."""
    local_path = LOCAL_REPO_PATH
    if not os.path.exists(local_path):
        print(f"Cloning {repo_url} to {local_path}...")
        repo = Repo.clone_from(repo_url, local_path)
        print("Clone complete.")
    else:
        print(f"Repository already exists at {local_path}. Loading...")
        repo = Repo(local_path)
    return repo

# clone_or_load_repo() is called from main.py script passing the REPO URL as an argument.

"""Capture each file of the cloned repo and get the parsed details, metadata as a list of dictionaries"""

#!pip install langchain_text_splitters --quiet

import os
import sys
import gc
import json
import mimetypes
from google.colab import drive
from langchain_text_splitters import RecursiveCharacterTextSplitter

def create_processed_repo_files():
    # Install Tree-sitter requirements
    #!pip install tree-sitter tree-sitter-python tree-sitter-typescript tree-sitter-go tree-sitter-java tree-sitter-c_sharp tree-sitter-rust tree-sitter-c tree-sitter-cpp tree-sitter-ruby tree-sitter-kotlin tree-sitter-swift tree-sitter-zig tree-sitter-lua tree-sitter-elixir tree-sitter-perl tree-sitter-json tree-sitter-yaml tree-sitter-toml tree-sitter-hcl tree-sitter-sql tree-sitter-dockerfile tree-sitter-html tree-sitter-css tree-sitter-markdown tree-sitter-javascript --quiet

    # Mount Drive
    if not os.path.exists('/content/drive'):
        drive.mount('/content/drive')

    # Add the Drive directory containing codeparser.py to sys.path
    # Change this path to the exact folder where codeparser.py is stored
    CODEPARSER_FOLDER = "/content/drive/MyDrive/Personal_Project"

    if CODEPARSER_FOLDER not in sys.path:
        sys.path.append(CODEPARSER_FOLDER)

    # Import the parser function
    from codeparser import parse_codebase_file

    # DIRECTORY CONFIGURATIONS & TIER BALANCING
    SOURCE_CODEBASE_DIR = "/content/drive/MyDrive/Personal_Project/target_repo/"
    OUTPUT_STAGE1_DIR = "/content/drive/MyDrive/Personal_Project/repo_files_parsed_output/"
    os.makedirs(OUTPUT_STAGE1_DIR, exist_ok=True)

    def get_processing_tier(filepath):
        parser_extensions = {
            '.py', '.java', '.cpp', '.hpp', '.cc', '.cxx', '.rs', '.go', '.c', '.h',
            '.js', '.ts', '.jsx', '.tsx', '.cs', '.rb', '.css', '.scss', '.less', '.html',
            '.json', '.md', '.sql', '.csv'
        }
        ext = os.path.splitext(filepath)[1].lower()
        if ext in parser_extensions:
            return "TIER_1_PARSER"
        mime_type, _ = mimetypes.guess_type(filepath)
        if (mime_type and mime_type.startswith('text/')) or ext in {'.yaml', '.yml', '.ini', '.conf', '.sh', '.bat', '.env', '.properties', '.txt'}:
            return "TIER_2_TEXT"
        return "TIER_3_BINARY"

    # SEQUENTIAL ALL-FILE INGESTION LOOP
    print(f"\nScanning all codebase pathways under: {SOURCE_CODEBASE_DIR}...")

    for root, _, files in os.walk(SOURCE_CODEBASE_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            relative_path = os.path.relpath(full_path, SOURCE_CODEBASE_DIR)
            tier = get_processing_tier(full_path)

            parsed_payload = []

            try:
                # --- TIER 1: Tree-Sitter AST Parsing (With Accurate Scope Ranges) ---
                if tier == "TIER_1_PARSER":
                    print(f"Analyzing AST Structure: {relative_path}...")

                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            file_content = f.read()

                        entities = parse_codebase_file(relative_path, file_content)

                        if entities and isinstance(entities, list):
                            CHUNK_SIZE = 1500
                            CHUNK_OVERLAP = 200

                            for entity in entities:
                                body = entity.get("implementation_body", "") or ""
                                docstring = entity.get("associated_docstring", "") or ""
                                parent_scope = entity.get("scope_range", {})
                                entity_length = len(body)

                                # --- 1. Single Chunk Path: Fits within limit ---
                                if entity_length <= CHUNK_SIZE:
                                    parsed_payload.append({
                                        "id": f"{entity.get('id')}__CODE",
                                        "name": entity.get("name"),
                                        "entity_type": entity.get("entity_type"),
                                        "scope_range": parent_scope,
                                        "signature": entity.get("signature"),
                                        "implementation_body": body.strip(),
                                        "associated_docstring": docstring,
                                        "parent_id": entity.get("parent_id"),
                                        "parent_type": entity.get("parent_type"),
                                        "chunk_metadata": {
                                            "chunk_index": 0,
                                            "total_chunks": 1,
                                            "is_subchunked": False
                                        }
                                    })

                                # --- 2. Multi-Chunk Path: Exceeds CHUNK_SIZE ---
                                else:
                                    ast_splitter = RecursiveCharacterTextSplitter(
                                        chunk_size=CHUNK_SIZE,
                                        chunk_overlap=CHUNK_OVERLAP,
                                        separators=["\n    def ", "\n    async def ", "\n\n", "\n", " ", ""]
                                    )

                                    chunks = ast_splitter.split_text(body)

                                    # CREATE LOGICAL / CANONICAL AST PARENT NODE
                                    parsed_payload.append({
                                        "id": f"{entity.get('id')}__CODE",
                                        "name": entity.get("name"),
                                        "entity_type": entity.get("entity_type"),
                                        "scope_range": parent_scope,
                                        "signature": entity.get("signature"),
                                        "implementation_body": "",
                                        "associated_docstring": "",
                                        "parent_id": entity.get("parent_id"),
                                        "parent_type": entity.get("parent_type"),
                                        "chunk_metadata": {
                                            "chunk_index": 0,
                                            "total_chunks": len(chunks),
                                            "is_subchunked": True
                                        }
                                    })

                                    # Parent baseline positioning
                                    entity_start_line = parent_scope.get("start_line", 1)
                                    current_char_offset = 0

                                    for chunk_index, chunk_text in enumerate(chunks):
                                        # Locate chunk within original entity body to get accurate offset
                                        found_idx = body.find(chunk_text, current_char_offset)
                                        if found_idx != -1:
                                            current_char_offset = found_idx

                                        # Calculate lines leading up to this sub-chunk within the body
                                        text_leading_up = body[:current_char_offset]
                                        lines_before = len(text_leading_up.splitlines()) - 1 if text_leading_up else 0
                                        if lines_before < 0:
                                            lines_before = 0

                                        # Determine start and end lines relative to full file
                                        chunk_start_line = entity_start_line + lines_before
                                        chunk_internal_lines = len(chunk_text.splitlines())
                                        chunk_end_line = chunk_start_line + max(0, chunk_internal_lines - 1)

                                        # Determine column offsets
                                        chunk_lines = chunk_text.split('\n')

                                        # Start column calculation
                                        if lines_before == 0:
                                            # First chunk starts at the parent entity's starting column
                                            chunk_start_col = parent_scope.get("start_column", 0)
                                        else:
                                            # Subsequent chunks starting on a new line begin at column 0
                                            chunk_start_col = 0

                                        chunk_end_col = len(chunk_lines[-1]) if chunk_lines else 0

                                        parsed_payload.append({
                                            "id": f"{entity.get('id')}__CODE_PART_{chunk_index}",
                                            "name": f"{entity.get('name')} (Part {chunk_index})",
                                            "entity_type": f"{entity.get('entity_type')}_CHUNK",
                                            "scope_range": {
                                                "start_line": chunk_start_line,
                                                "start_column": chunk_start_col,
                                                "end_line": chunk_end_line,
                                                "end_column": chunk_end_col
                                            },
                                            "signature": entity.get("signature"),
                                            "implementation_body": chunk_text.strip(),
                                            "associated_docstring": docstring if chunk_index == 0 else f"Recursive AST split segment index tracking marker: {chunk_index}",
                                            "parent_id": entity.get("id"),
                                            "parent_type": entity.get("entity_type"),
                                            "chunk_metadata": {
                                                "chunk_index": chunk_index,
                                                "total_chunks": len(chunks),
                                                "is_subchunked": True
                                            }
                                        })

                                        # Advance offset accounting for overlap
                                        current_char_offset += max(1, len(chunk_text) - CHUNK_OVERLAP)

                        else:
                            tier = "TIER_2_TEXT"
                    except Exception as parse_err:
                        print(f" [AST Fallback] {relative_path} failed AST parsing ({parse_err}). Falling back to TIER_2_TEXT.")
                        tier = "TIER_2_TEXT"

                # --- TIER 2: General Text Files (Recursive Chunking) ---
                if tier == "TIER_2_TEXT":
                    CHUNK_SIZE = 1500
                    CHUNK_OVERLAP = 200

                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        full_text = f.read()

                    text_length = len(full_text)
                    total_lines = len(full_text.splitlines())

                    if text_length <= CHUNK_SIZE:
                        parsed_payload.append({
                            "id": f"{relative_path}#DOCUMENT",
                            "name": file,
                            "entity_type": "TEXT_DOCUMENT",
                            "scope_range": {
                                "start_line": 1,
                                "start_column": 0,
                                "end_line": total_lines if total_lines > 0 else 1,
                                "end_column": len(full_text.split('\n')[-1]) if total_lines > 0 else 0
                            },
                            "signature": f"TEXT FILE: {relative_path}",
                            "implementation_body": full_text.strip(),
                            "associated_docstring": "",
                            "parent_id": f"{relative_path}#FILE",
                            "parent_type": "FILE",
                            "chunk_metadata": {
                                "chunk_index": 0,
                                "total_chunks": 1,
                                "is_subchunked": False
                            }
                        })
                    else:
                        splitter = RecursiveCharacterTextSplitter(
                            chunk_size=CHUNK_SIZE,
                            chunk_overlap=CHUNK_OVERLAP,
                            separators=["\n\n", "\n", " ", ""]
                        )
                        chunks = splitter.split_text(full_text)

                        # CREATE LOGICAL / CANONICAL DOCUMENT PARENT NODE
                        parsed_payload.append({
                            "id": f"{relative_path}#DOCUMENT",
                            "name": file,
                            "entity_type": "TEXT_DOCUMENT",
                            "scope_range": {
                                "start_line": 1,
                                "start_column": 0,
                                "end_line": total_lines if total_lines > 0 else 1,
                                "end_column": len(full_text.split("\n")[-1]) if total_lines > 0 else 0
                            },
                            "signature": f"TEXT FILE: {relative_path}",
                            "implementation_body": "",
                            "associated_docstring": "",
                            "parent_id": f"{relative_path}#FILE",
                            "parent_type": "FILE",
                            "chunk_metadata": {
                                "chunk_index": 0,
                                "total_chunks": len(chunks),
                                "is_subchunked": True
                            }
                        })

                        current_char_offset = 0

                        for chunk_index, chunk_text in enumerate(chunks):
                            found_idx = full_text.find(chunk_text, current_char_offset)
                            if found_idx != -1:
                                current_char_offset = found_idx

                            text_leading_up = full_text[:current_char_offset]
                            chunk_start_line = len(text_leading_up.splitlines()) if text_leading_up else 1
                            chunk_internal_lines = len(chunk_text.splitlines())
                            chunk_end_line = chunk_start_line + max(0, chunk_internal_lines - 1)
                            last_line_content = chunk_text.split('\n')[-1]
                            chunk_end_column = len(last_line_content)

                            parsed_payload.append({
                                "id": f"{relative_path}#DOCUMENT_PART_{chunk_index}",
                                "name": f"{file} (Part {chunk_index})",
                                "entity_type": "TEXT_DOCUMENT_CHUNK",
                                "scope_range": {
                                    "start_line": chunk_start_line,
                                    "start_column": 0,
                                    "end_line": chunk_end_line,
                                    "end_column": chunk_end_column
                                },
                                "signature": f"TEXT FILE: {relative_path} [Chunk {chunk_index}]",
                                "implementation_body": chunk_text.strip(),
                                "associated_docstring": f"Recursive text split segment index tracking marker: {chunk_index}",
                                "parent_id": f"{relative_path}#FILE",
                                "parent_type": "FILE",
                                "chunk_metadata": {
                                    "chunk_index": chunk_index,
                                    "total_chunks": len(chunks),
                                    "is_subchunked": True
                                }
                            })
                            current_char_offset += max(1, len(chunk_text) - CHUNK_OVERLAP)

                # --- TIER 3: Binary Files / Assets ---
                elif tier == "TIER_3_BINARY":
                    file_size_bytes = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                    contextual_desc = (
                        f"Binary asset record metadata node reference named {file} "
                        f"situated at directory sequence path {relative_path}. "
                        f"File size: {file_size_bytes} bytes."
                    )

                    parsed_payload.append({
                        "id": f"{relative_path}#ASSET",
                        "name": file,
                        "entity_type": "BINARY_ASSET",
                        "scope_range": {
                            "start_line": 1,
                            "start_column": 0,
                            "end_line": 1,
                            "end_column": 0
                        },
                        "signature": f"BINARY FILE: {relative_path}",
                        "implementation_body": contextual_desc,
                        "associated_docstring": f"Non-text file data element asset reference block. Size: {file_size_bytes} bytes.",
                        "parent_id": f"{relative_path}#FILE",
                        "parent_type": "FILE",
                        "chunk_metadata": {
                            "chunk_index": 0,
                            "total_chunks": 1,
                            "is_subchunked": False
                        }
                    })

                # --- Write intermediate parsed JSON output to Google Drive to save memory---
                if parsed_payload:
                    safe_filename = relative_path.replace("/", "_").replace("\\", "_") + "_staged.json"
                    output_file_path = os.path.join(OUTPUT_STAGE1_DIR, safe_filename)

                    with open(output_file_path, 'w', encoding='utf-8') as out_f:
                        json.dump(parsed_payload, out_f, indent=2)

                    print(f" Staged File Registry: {relative_path} -> {safe_filename} [{tier}]")

            except Exception as e:
                print(f" Failed executing parsing on {relative_path}: {e}")

            finally:
                # To prevent memory overflow
                del parsed_payload
                gc.collect()

    print(f"\nAll Repo Files Parsed Successfully within: {OUTPUT_STAGE1_DIR}")
    
# create_processed_repo_files() is called from main.py script.