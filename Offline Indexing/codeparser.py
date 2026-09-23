import abc
import re
from tree_sitter import Node
from tree_sitter import Language, Query, Parser, QueryCursor
import tree_sitter_python as tspython
import tree_sitter_go as tsgo
import tree_sitter_c as tsc
import tree_sitter_cpp as tscpp
import tree_sitter_java as tsjava
import tree_sitter_rust as tsrust
import tree_sitter_typescript as tsts
import tree_sitter_javascript as tsjs
import tree_sitter_ruby as tsruby
import tree_sitter_css as tscss
import tree_sitter_html as tshtmls
import tree_sitter_json as tsjson
import tree_sitter_markdown as tsmarkdown
import tree_sitter_sql as tssql

class BaseLanguageHandler(abc.ABC):
    NODE_MAPPINGS = {}

    def __init__(self, file_path: str, file_content: str):
        self.file_path = file_path.replace("\\", "/")
        # Convert to bytes immediately to safeguard byte-offset slicing
        self.file_content = file_content.encode('utf-8') if isinstance(file_content, str) else file_content
        self.entities = []
        self.comment_map = {}

    def get_category(self, node: Node) -> str | None:
        for cat, tokens in self.NODE_MAPPINGS.items():
            if node.type in tokens:
                return cat
        return None
    def get_clean_text(self, node: Node) -> str:
        if not node:
            return ""

        start = node.start_byte
        end = node.end_byte
        size = end - start

        print(
            f"TEXT REQUEST: {node.type} start={start} end={end} size={size}",
            flush=True
        )

        text_slice = self.file_content[start:end]

        print(
            f"SLICE DONE len={len(text_slice)}",
            flush=True
        )

        text = text_slice.decode("utf-8", errors="ignore")

        print(
            f"DECODE DONE len={len(text)}",
            flush=True
        )

        return text
    

    def build_comment_map(self, root_node):
        self.comment_map = {}

        COMMENT_TYPES = {
            "comment",
            "line_comment",
            "block_comment",
            "hash_comment",
            "document_comment",
        }

        stack = [root_node]

        while stack:

            node = stack.pop()

            children = getattr(node, "children", None)

            if children:

                previous_comment_group = []

                for child in children:

                    if child.type in COMMENT_TYPES:
                        previous_comment_group.append(child)

                    else:

                        if previous_comment_group:
                            self.comment_map[child.id] = (
                                previous_comment_group.copy()
                            )
                            previous_comment_group.clear()

                for child in reversed(children):
                    stack.append(child)
                    
    def get_docstring(self, node: Node) -> str:
        comments = self.comment_map.get(node.id)

        if not comments:
            return ""

        raw_docstring = "\n".join(
            self.get_clean_text(comment).strip()
            for comment in comments
        )

        cleaned = re.sub(
            r"</?[a-zA-Z0-9_-]+>",
            "",
            raw_docstring,
        )

        cleaned = re.sub(
            r"^/{2,3}\s*|^\s*\*?\s*|^\s*#\s*|^\s*--\s*",
            "",
            cleaned,
            flags=re.MULTILINE,
        )

        return (
            cleaned
            .replace("/**", "")
            .replace("*/", "")
            .replace("/*", "")
            .strip("*/ \n\r\t")
        )

    def extract_fields(self, node: Node, category: str, name: str):

        outermost_node = node
        while outermost_node.parent and outermost_node.parent.type in ["export_statement", "lexical_declaration"]:
            outermost_node = outermost_node.parent

        full_text = self.get_clean_text(outermost_node).strip()
        body_node = next((node.child_by_field_name(f) for f in ["body", "block", "compound_statement"] if node.child_by_field_name(f)), None)

        if body_node:
            # CRASH PROTECT: Ensure start is always less than end to prevent negative string slicing/C-buffer crashes
            start = outermost_node.start_byte
            end = body_node.start_byte
            if start <= end:
                signature = self.file_content[start:end].decode('utf-8', errors='ignore').strip()
            else:
                signature = full_text.splitlines()[0] if full_text else ""

            if signature.endswith("{") or signature.endswith(":"):
                signature = signature[:-1].strip()
        else:
            signature = full_text.splitlines()[0] if full_text else ""

        if category in ["class", "struct", "interface"] and not full_text.endswith(";"):
            if self.file_path.endswith(".py"):
                impl_body = f"{signature}:\n    # Structural parsing target details separated dynamically"
            else:
                impl_body = f"{signature} {{\n  /* Structural parsing target details separated dynamically */\n}}"
        else:
            impl_body = full_text

        return signature, impl_body

    def get_node_name(self, node: Node) -> str:
        name_node = node.child_by_field_name("name") or node.child_by_field_name("declarator") or node.child_by_field_name("id")
        if not name_node:
            name_node = next((c for c in node.named_children if c.type in ["type_identifier", "identifier"]), None)
        return self.get_clean_text(name_node).strip() if name_node else "anonymous"

    def build_entity_record(self, node: Node, category: str, name: str, parent_id: str, parent_type: str, scope_prefix: str, complexity: int = 1):
        print(
            f"ENTER build_entity_record {category}:{name}",
            flush=True
        )
        signature, impl_body = self.extract_fields(node, category, name)
        print(
            f"AFTER extract_fields {category}:{name}",
            flush=True
        )
        unique_id = f"{self.file_path}#{scope_prefix}{name}"
        
        print("BEFORE get_docstring", flush=True)
        doc = self.get_docstring(node)
        print("AFTER get_docstring", flush=True)

        return {
            "id": unique_id,
            "name": name,
            "entity_type": category.upper(),
            "scope_range": {
                "start_line": node.start_point.row + 1, "start_column": node.start_point.column,
                "end_line": node.end_point.row + 1, "end_column": node.end_point.column
            },
            "signature": signature,
            "implementation_body": impl_body,
            "associated_docstring": self.get_docstring(node),
            "parent_id": parent_id,
            "parent_type": parent_type
        }

    @abc.abstractmethod
    def parse(self, tree) -> list:
        pass

class PythonHandler(BaseLanguageHandler):

    QUERY_SOURCE = """
    (class_definition
        name: (identifier) @name) @definition

    (function_definition
        name: (identifier) @name) @definition
    """
    
    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body_node = node.child_by_field_name("body")
        if body_node:
            start = node.start_byte
            end = body_node.start_byte
            if start < end:
                sig = self.file_content[start:end].decode("utf-8", errors="ignore").strip()
                return sig.rstrip(":")
        return self.file_content[node.start_byte:node.end_byte]\
                   .decode("utf-8", errors="ignore")\
                   .splitlines()[0]

    def _get_python_docstring(self, node):  # REPLACE existing method with this
            # 1. Try actual docstring first
            body = node.child_by_field_name("body")
            if body and body.children:
                first = body.children[0]
                if first.type == "expression_statement" and first.children:
                    string_node = first.children[0]
                    if string_node.type == "string":
                        text = self.file_content[
                            string_node.start_byte:string_node.end_byte
                        ].decode("utf-8", errors="ignore").strip()
                        for delim in ['"""', "'''", '"', "'"]:
                            if text.startswith(delim) and text.endswith(delim) \
                                    and len(text) > 2 * len(delim):
                                return text[len(delim):-len(delim)].strip()
                        return text

            # 2. Fall back to # comments above the node
            return self._get_preceding_comments(node)
        
    def _get_preceding_comments(self, node):  # ADD this new method
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        node_idx = next(
            (i for i, c in enumerate(siblings) if c.id == node.id), None
        )
        if node_idx is None:
            return ""

        comment_lines = []
        for sibling in reversed(siblings[:node_idx]):
            if sibling.type == "comment":
                text = self.file_content[
                    sibling.start_byte:sibling.end_byte
                ].decode("utf-8", errors="ignore").strip()
                text = text.lstrip("#").strip()
                comment_lines.append(text)
            else:
                break

        if not comment_lines:
            return ""

        return "\n".join(reversed(comment_lines))
    
    def _strip_docstring_from_body(self, body: str) -> str:
        """Remove the leading docstring from implementation body since it's stored separately."""
        import ast
        try:
            tree = ast.parse(body)
            if (tree.body and isinstance(tree.body[0], ast.FunctionDef)
                    and tree.body[0].body
                    and isinstance(tree.body[0].body[0], ast.Expr)
                    and isinstance(tree.body[0].body[0].value, ast.Constant)):
                # find the docstring end line and strip it
                doc_node = tree.body[0].body[0]
                lines = body.splitlines()
                # keep signature + everything after the docstring
                after_doc = lines[doc_node.end_lineno:]
                sig_end = next(i for i, l in enumerate(lines) if l.rstrip().endswith(":"))
                return "\n".join(lines[:sig_end+1] + after_doc).strip()
        except Exception:
            pass
        return body

    def _get_implementation_body(self, node: Node) -> str:
        try:
            if node.type == "class_definition":
                body_node = node.child_by_field_name("body")
                if body_node:
                    first_method = next(
                        (c for c in body_node.children if c.type == "function_definition"),
                        None
                    )
                    end = first_method.start_byte if first_method else body_node.end_byte
                    return self.file_content[node.start_byte:end]\
                            .decode("utf-8", errors="ignore").strip()
                return ""
            else:
                body = self.file_content[node.start_byte:node.end_byte]\
                        .decode("utf-8", errors="ignore")
                return self._strip_docstring_from_body(body)  # ← here
        except Exception as e:
            print(f"FAILED implementation_body -> {e}", flush=True)
            return ""


    def parse(self, tree) -> list:

        lang = Language(tspython.language())
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        seen_ids = set()
        # scope stack: list of dicts with node info
        # each entry: {end_byte, entity_id, entity_type, name}
        scope_stack = []

        print("has_error:", tree.root_node.has_error)
        
        def get_current_parent():
            if not scope_stack:
                return f"{self.file_path}#FILE", "FILE"
            return scope_stack[-1]["entity_id"], scope_stack[-1]["entity_type"]

        def push_scope(node, entity_id, entity_type, name):
            scope_stack.append({
                "end_byte": node.end_byte,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "name": name,
            })

        def pop_expired_scopes(current_start_byte):
            # pop any scopes that ended before the current node starts
            while scope_stack and scope_stack[-1]["end_byte"] <= current_start_byte:
                scope_stack.pop()

        for _pattern_idx, capture_dict in matches:
            def_nodes = capture_dict.get("definition", [])
            name_nodes = capture_dict.get("name", [])

            if not def_nodes or not name_nodes:
                continue

            node = def_nodes[0]
            name_node = name_nodes[0]

            if node.id in seen_ids:
                continue
            seen_ids.add(node.id)

            name = self.file_content[name_node.start_byte:name_node.end_byte]\
                    .decode("utf-8", errors="ignore").strip()

            # pop scopes that have ended before this node
            pop_expired_scopes(node.start_byte)

            # determine category from current scope + node type
            parent_id, parent_type = get_current_parent()

            if node.type == "class_definition":
                category = "class"
                entity_id = f"{parent_id}.{name}:{node.start_point[0] + 1}:{node.start_point[1]}" if parent_type != "FILE" else f"{self.file_path}#{name}:{node.start_point[0] + 1}:{node.start_point[1]}"

            elif node.type == "function_definition":
                if parent_type == "CLASS":
                    category = "method"
                elif parent_type in ("FUNCTION", "METHOD"):
                    category = "nested_function"
                else:
                    category = "function"
                entity_id = f"{parent_id}.{name}:{node.start_point[0] + 1}:{node.start_point[1]}"  # always dot-chain from parent

            else:
                continue

            try:
                signature = self._get_signature(node)
                docstring = self._get_python_docstring(node)

                self.entities.append({
                    "id": entity_id,
                    "name": name,
                    "entity_type": category.upper(),
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": self._get_implementation_body(node),
                    "associated_docstring": docstring,
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                })

                # push this node onto the scope stack so children see it as parent
                push_scope(node, entity_id, category.upper(), name)

            except Exception as e:
                print(f"FAILED ENTITY {category}:{name} -> {e}", flush=True)
                
        # Fallback: Capture Python module script if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* Python Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source code line order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
                
        print(f"RETURNING {len(self.entities)} ENTITIES", flush=True)
        return self.entities

class GoHandler(BaseLanguageHandler):
    
    QUERY_SOURCE = r"""
        (type_spec) @definition
        (function_declaration) @definition
        (method_declaration) @definition
        (func_literal) @definition
        """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:

        body = node.child_by_field_name("body")

        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode(
                "utf-8",
                errors="ignore"
            ).strip()

        text = self._node_text(node)

        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        target = node
        if (
            node.parent
            and node.parent.type == "type_declaration"
        ):
            target = node.parent
        parent = target.parent
        if not parent:
            return ""
        siblings = list(parent.children)
        try:
            idx = siblings.index(target)
        except ValueError:
            return ""
        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type != "comment":
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif (
                text.startswith("/*")
                and text.endswith("*/")
            ):
                text = text[2:-2].strip()
            comments.append(text)
        comments.reverse()
        return "\n".join(comments)

    def _extract_receiver_type(self, node: Node) -> str:
        receiver = node.child_by_field_name("receiver")
        if not receiver:
            return ""
        text = self._node_text(receiver)
        # (db *DataBuffer[T]) -> (*DataBuffer[T])
        text = re.sub(r"^\(\s*\w+\s+", "(", text)
        # Remove generic params
        text = re.sub(r"\[.*?\]", "", text)
        # Remove pointer and parens
        text = text.replace("*", "")
        text = text.replace("(", "")
        text = text.replace(")", "")
        text = text.strip()

        return text.split(".")[-1]
    
    def _extract_closure_name(self, node):
        parent = node.parent
        if not parent:
            return None
        if parent.type != "expression_list":
            return None
        grand_parent = parent.parent
        if not grand_parent:
            return None
        if grand_parent.type != "short_var_declaration":
            return None
        lhs = grand_parent.children[0]
        for child in lhs.children:
            if child.type == "identifier":
                return self._node_text(child)
        return None

    def _classify_type(self, node: Node) -> str:
        text = self._node_text(node)
        type_node = node.child_by_field_name("type")
        if type_node:
            if type_node.type == "struct_type":
                return "STRUCT"
            if type_node.type == "interface_type":
                body = self._node_text(type_node)
                if "~" in body or "|" in body:
                    return "CONSTRAINT"
                return "INTERFACE"
        # explicit alias syntax
        if "=" in text:
            return "TYPE_ALIAS"
        return "TYPE_ALIAS"

    def _extract_name(self, node: Node) -> str:
        # anonymous closures
        if node.type == "func_literal":
            return f"closure_{node.start_point[0]+1}"
        # try field names first
        for field in ("name",):
            child = node.child_by_field_name(field)
            if child:
                return self._node_text(child).strip()
        # fallback to child inspection
        for child in node.children:
            if child.type in (
                "identifier",
                "type_identifier",
                "field_identifier",
            ):
                return self._node_text(child).strip()

        return ""

    def _entity_exists(self, name: str) -> bool:
        for entity in self.entities:
            if entity["name"] == name:
                return True

        return False


    def _get_line_column(self, byte_position: int):
        source = self.file_content.decode(
            "utf-8",
            errors="ignore",
        )
        line = source[:byte_position].count("\n") + 1
        last_newline = source.rfind(
            "\n",
            0,
            byte_position,
        )
        if last_newline == -1:
            column = byte_position
        else:
            column = byte_position - last_newline - 1

        return line, column


    def _recover_type_aliases(self):
        source = self.file_content.decode(
            "utf-8",
            errors="ignore",
        )
        #
        # Matches:
        #
        # type UUID = string
        # type Timestamp=int64
        # type MyError = error
        #
        pattern = (
            r"type\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*=\s*"
            r"([^\n]+)"
        )
        for match in re.finditer(pattern, source):
            name = match.group(1).strip()
            alias_type = match.group(2).strip()
            #
            # already captured by tree-sitter
            #
            if self._entity_exists(name):
                continue
            start_byte = match.start()
            end_byte = match.end()
            start_line, start_column = (
                self._get_line_column(start_byte)
            )
            end_line, end_column = (
                self._get_line_column(end_byte)
            )
            implementation = (
                match.group(0).strip()
            )
            self.entities.append(
                {
                    "id":
                        f"{self.file_path}#{name}",

                    "name":
                        name,

                    "entity_type":
                        "TYPE_ALIAS",

                    "scope_range":
                        {
                            "start_line":
                                start_line,

                            "start_column":
                                start_column,

                            "end_line":
                                end_line,

                            "end_column":
                                end_column,
                        },

                    "signature":
                        implementation,

                    "implementation_body":
                        implementation,

                    "associated_docstring":
                        "",

                    "parent_id":
                        f"{self.file_path}#FILE",

                    "parent_type":
                        "FILE",
                }
            )
            
    def _find_entity_id(self, name):
        for entity in self.entities:
            if entity["name"] == name:
                return (
                    entity["id"],
                    entity["entity_type"]
                )

        return None,None
        
    def parse(self, tree):

        lang = Language(tsgo.language())
        #print(tsgo.language())

        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #    print(child.type)
        
        ordered = []

        for pattern_idx, capture_dict in matches:

            defs = capture_dict.get("definition", [])

            if not defs:
                continue

            ordered.append(
                (
                    defs[0].start_byte,
                    pattern_idx,
                    capture_dict,
                )
            )

        ordered.sort(key=lambda x: x[0])

        self.entities = []

        seen = set()
        scope_stack = []

        def pop_scopes(pos):

            while (
                scope_stack
                and scope_stack[-1]["end"]
                <= pos
            ):
                scope_stack.pop()

        def current_parent():

            if not scope_stack:
                return (
                    f"{self.file_path}#FILE",
                    "FILE",
                )

            return (
                scope_stack[-1]["id"],
                scope_stack[-1]["type"],
            )

        def push_scope(node, entity_id, entity_type):

            scope_stack.append(
                {
                    "end": node.end_byte,
                    "id": entity_id,
                    "type": entity_type,
                }
            )

        for _, _, captures in ordered:

            node = captures["definition"][0]

            if node.id in seen:
                continue

            seen.add(node.id)

            pop_scopes(node.start_byte)

            parent_id, parent_type = current_parent()

            name = ""

            name = self._extract_name(node)

            # try recovering closure names
            if node.type == "func_literal":

                closure_name = self._extract_closure_name(node)

                if closure_name:
                    name = closure_name

            if not name:
                continue
            
            if node.type == "type_spec":

                entity_type = self._classify_type(node)

            elif node.type == "function_declaration":

                entity_type = "FUNCTION"

            elif node.type == "method_declaration":

                entity_type = "METHOD"

            elif node.type == "func_literal":

                entity_type = "CLOSURE"

            else:
                continue

            if entity_type=="METHOD":
                receiver = (
                    self._extract_receiver_type(
                        node
                    )
                )
                if receiver:
                    result = self._find_entity_id(receiver)
                    if result:
                        parent_id,parent_type = result
                    else:
                        parent_id = (
                            f"{self.file_path}#{receiver}"
                        )
                        parent_type = "STRUCT"
                        
                entity_id = (
                f"{parent_id}.{name}:{node.start_point[0]+1}:{node.start_point[1]}"
            )
            elif entity_type=="CLOSURE":

                entity_id = (
                    f"{parent_id}.{name}:{node.start_point[0] + 1}:{node.start_point[1]}"
                )
            elif parent_type=="FILE":

                entity_id = (
                    f"{self.file_path}#{name}:{node.start_point[0] + 1}:{node.start_point[1]}"
                )
            else:

                entity_id = (
                    f"{parent_id}.{name}:{node.start_point[0] + 1}:{node.start_point[1]}"
                )

            try:

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line":
                            node.start_point[0] + 1,
                        "start_column":
                            node.start_point[1],
                        "end_line":
                            node.end_point[0] + 1,
                        "end_column":
                            node.end_point[1],
                    },
                    "signature":
                        self._get_signature(node),
                    "implementation_body":
                        self._node_text(node),
                    "associated_docstring":
                        self._get_preceding_comments(node),
                    "parent_id":
                        parent_id,
                    "parent_type":
                        parent_type,
                }

                self.entities.append(entity)

                if entity_type in (
                    "FUNCTION",
                    "METHOD",
                    "STRUCT",
                    "INTERFACE"
                ):

                    push_scope(
                        node,
                        entity_id,
                        entity_type,
                    )

            except Exception as exc:

                print(
                    f"FAILED {name}: {exc}",
                    flush=True,
                )
                

        # recover aliases
        self._recover_type_aliases()

        # Fallback: Capture Go module script if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* Go Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # preserve source order
        self.entities.sort(
            key=lambda entity: (
                entity["scope_range"]["start_line"],
                entity["scope_range"]["start_column"],
            )
        )

        #for e in self.entities:
        #    print(e["entity_type"], e["name"])

        return self.entities

class CHandler(BaseLanguageHandler):

    # Corrected 'typedef_declaration' -> 'type_definition'
    QUERY_SOURCE = r"""
    ;; Captures typedef declarations: typedef struct/enum/union/alias
    (type_definition) @definition

    ;; Captures standalone struct definitions: struct Foo { ... };
    (struct_specifier) @definition

    ;; Captures standalone enum definitions: enum Bar { ... };
    (enum_specifier) @definition

    ;; Captures standalone union definitions: union Baz { ... };
    (union_specifier) @definition

    ;; Captures function definitions
    (function_definition) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode("utf-8", errors="ignore").strip()

        text = self._node_text(node)
        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type != "comment":
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                text = text[2:-2].strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_function_name(self, node: Node) -> str:
        """Recursively unwraps declarators to extract the function identifier."""
        declarator = node.child_by_field_name("declarator")
        while declarator:
            if declarator.type == "identifier":
                return self._node_text(declarator).strip()
            
            # Recurse through pointer, parenthesized, array, or function declarator wrappers
            next_decl = declarator.child_by_field_name("declarator")
            if not next_decl:
                # Direct search inside children
                for child in declarator.children:
                    if child.type == "identifier":
                        return self._node_text(child).strip()
            declarator = next_decl

        return ""
    

    def _extract_typedef_info(self, node: Node):
        """Extract name and category from a type_definition (typedef) node."""
        type_identifier_node = None
        specifier = None

        def search_type_identifier(curr_node):
            nonlocal type_identifier_node
            if curr_node.type in ("type_identifier", "primitive_type"):
                type_identifier_node = curr_node
                return
            for child in curr_node.children:
                search_type_identifier(child)
                if type_identifier_node:
                    return

        for child in node.children:
            if child.type in ("struct_specifier", "enum_specifier", "union_specifier"):
                specifier = child
            elif child.type == "type_identifier":
                type_identifier_node = child
            elif child.type in ("type_declarator", "pointer_declarator"):
                search_type_identifier(child)

        name = self._node_text(type_identifier_node).strip() if type_identifier_node else ""

        if specifier:
            spec_type = specifier.type
            if spec_type == "struct_specifier":
                category = "STRUCT"
            elif spec_type == "enum_specifier":
                category = "ENUM"
            elif spec_type == "union_specifier":
                category = "UNION"
            else:
                category = "TYPE_ALIAS"
        else:
            category = "TYPE_ALIAS"

        return name, category, specifier

    def _extract_name(self, node: Node) -> str:
        if node.type == "function_definition":
            return self._extract_function_name(node)

        if node.type in ("struct_specifier", "enum_specifier", "union_specifier"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._node_text(name_node).strip()

        return ""
    
    def _get_typedef_signature(self, node, name, entity_type):
        if entity_type == "STRUCT":
            return f"typedef struct {name}"

        if entity_type == "ENUM":
            return f"typedef enum {name}"

        if entity_type == "UNION":
            return f"typedef union {name}"

        if entity_type == "TYPE_ALIAS":
            return f"typedef {name}"

        return self._get_signature(node)
    
    def parse(self, tree) -> list:
        lang = Language(tsc.language())  # Adjust binding call to match your loader
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        definitions = []
        declarations = []
        macros = []

        for pattern_idx, capture_dict in matches:

            if "definition" in capture_dict:
                definitions.append(
                    (
                        pattern_idx,
                        capture_dict["definition"][0]
                    )
                )

            if "declaration" in capture_dict:
                declarations.append(
                    (
                        pattern_idx,
                        capture_dict["declaration"][0]
                    )
                )

            if "macro" in capture_dict:
                macros.append(
                    (
                        pattern_idx,
                        capture_dict["macro"][0]
                    )
                )
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #   print(child.type)

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()
        processed_inner_specifiers = set()

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen or node.id in processed_inner_specifiers:
                continue

            name = ""
            entity_type = ""

            # 1. Handle Typedef Declarations (type_definition)
            if node.type == "type_definition":
                name, entity_type, inner_specifier = self._extract_typedef_info(node)
                if inner_specifier:
                    processed_inner_specifiers.add(inner_specifier.id)

            # 2. Handle Standalone Structs, Enums, Unions
            elif node.type in (
                    "struct_specifier",
                    "enum_specifier",
                    "union_specifier"
            ):
                # Ignore references like:
                #
                # struct Node *next;
                # struct TreeNode *left;
                #
                if (
                    node.parent is not None
                    and node.parent.type != "translation_unit"
                ):
                    continue

                name = self._extract_name(node)

                if not name:
                    continue

                type_map = {
                    "struct_specifier": "STRUCT",
                    "enum_specifier": "ENUM",
                    "union_specifier": "UNION"
                }

                entity_type = type_map[node.type]

            # 3. Handle Functions
            elif node.type == "function_definition":
                name = self._extract_name(node)
                entity_type = "FUNCTION"

            if not name or not entity_type:
                continue

            seen.add(node.id)

            parent_id = f"{self.file_path}#FILE"
            parent_type = "FILE"
            entity_id = (
                f"{self.file_path}"
                f"#{name}"
                f":{node.start_point[0]+1}"
                f":{node.start_point[1]}"
            )

            try:
                if entity_type in ("STRUCT", "ENUM", "UNION", "TYPE_ALIAS"):
                    signature = (
                        self._get_typedef_signature(
                            node,
                            name,
                            entity_type
                        )
                    )
                else:
                    signature = (
                        self._get_signature(node)
                    )

                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)
                

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture C module script if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* C Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class CPPHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures namespaces
    (namespace_definition) @definition

    ;; Captures class definitions
    (class_specifier) @definition

    ;; Captures struct definitions
    (struct_specifier) @definition

    ;; Captures enum definitions
    (enum_specifier) @definition

    ;; Captures union definitions
    (union_specifier) @definition

    ;; Captures typedefs and 'using' type aliases
    (type_definition) @definition
    (alias_declaration) @definition

    ;; Captures standalone function and method definitions
    (function_definition) @definition
    
    ;; Captures function declarations
    (declaration) @definition

    ;; Captures class member declarations
    (field_declaration) @definition

    ;; Captures template declarations wrapping classes or functions
    (template_declaration) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode("utf-8", errors="ignore").strip()

        text = self._node_text(node)
        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type != "comment":
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                text = text[2:-2].strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _find_declarator(self,node):
        if node is None:
            return None
        if node.type in (
            "function_declarator",
            "reference_declarator",
            "pointer_declarator"
        ):
            return node
        
        for child in node.children:
            result=self._find_declarator(
                child
            )
            if result:
                return result

        return None

    def _find_function_name(self,node):

        if node is None:
            return ""
        if node.type in (
            "identifier",
            "field_identifier",
            "destructor_name",
            "operator_name"

        ):

            return self._node_text(
                node
            ).strip()


        if node.type=="qualified_identifier":
            name_node=node.child_by_field_name(
                "name"
            )
            if name_node:
                return self._node_text(
                    name_node
                ).strip()


        for child in node.children:
            result=self._find_function_name(
                child
            )
            if result:
                return result


        return ""

    def _inside_class(self,node):
        parent=node.parent
        while parent:

            if parent.type in (
                "class_specifier",
                "struct_specifier"
            ):
                return True

            parent=parent.parent
        return False

    def _extract_function_info(self, node):

        declarator=node.child_by_field_name(
            "declarator"
        )
        if declarator is None:
            declarator=self._find_declarator(
                node
            )
            
        qualifier=""

        if declarator:
            for child in declarator.children:
                if child.type=="qualified_identifier":
                    scope_node=(
                        child.child_by_field_name(
                            "scope"
                        )
                    )
                    if scope_node:
                        qualifier=(
                            self._node_text(
                                scope_node
                            )
                            .strip()
                            .rstrip("::")
                        )
                    break

        name=self._find_function_name(
            declarator
        )
        
        is_method=self._inside_class(
            node
        )
        if qualifier or is_method:
            category="METHOD"
        else:
            category="FUNCTION"

        return (
            name,
            qualifier,
            category
        )
        
    def _contains_function_declarator(self,node):
        if node is None:
            return False
        if node.type=="function_declarator":
            return True
        for child in node.children:
            if self._contains_function_declarator(child):
                return True

        return False
        
    def _is_function_declaration(self,node):
        declarator=node.child_by_field_name(
            "declarator"
        )
        if declarator is None:
            declarator=node
        if not self._contains_function_declarator(
                declarator):
            return False
        name=self._find_function_name(
                declarator
        )

        return bool(name)

    def _extract_name_fallback(self, node: Node) -> str:
        for child in node.children:
            if child.type in ("identifier", "field_identifier", "type_identifier", "destructor_name"):
                return self._node_text(child).strip()
        return ""

    def _extract_typedef_alias_info(self, node: Node):
        """Extract info from type_definition (typedef) or alias_declaration (using X = Y)."""
        if node.type == "alias_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "TYPE_ALIAS", None

        # type_definition
        type_identifier_node = None
        specifier = None

        def search_type_identifier(curr_node):
            nonlocal type_identifier_node
            if curr_node.type in ("type_identifier", "primitive_type"):
                type_identifier_node = curr_node
                return
            for child in curr_node.children:
                search_type_identifier(child)
                if type_identifier_node:
                    return

        for child in node.children:
            if child.type in ("struct_specifier", "enum_specifier", "union_specifier", "class_specifier"):
                specifier = child
            elif child.type == "type_identifier":
                type_identifier_node = child
            elif child.type in ("type_declarator", "pointer_declarator"):
                search_type_identifier(child)

        name = self._node_text(type_identifier_node).strip() if type_identifier_node else ""

        if specifier:
            spec_map = {
                "class_specifier": "CLASS",
                "struct_specifier": "STRUCT",
                "enum_specifier": "ENUM",
                "union_specifier": "UNION",
            }
            category = spec_map.get(specifier.type, "TYPE_ALIAS")
        else:
            category = "TYPE_ALIAS"

        return name, category, specifier

    def _extract_name(self, node: Node) -> str:
        if node.type == "namespace_definition":
            name_node = node.child_by_field_name("name")
            return self._node_text(name_node).strip() if name_node else "anonymous_ns"

        if node.type in ("class_specifier", "struct_specifier", "enum_specifier", "union_specifier"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._node_text(name_node).strip()

        return ""
    
    def _contains_type_definition(self,node):
        if node is None:
            return False
        if node.type in (
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
            "union_specifier"
        ):
            return True
        for child in node.children:
            if self._contains_type_definition(child):
                return True

        return False

    def parse(self, tree) -> list:
        lang = Language(tscpp.language())  # Adjust binding path if using tree_sitter_cpp
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #   print(child.type)

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()
        processed_inner_specifiers = set()

        # Track structural scopes (Namespaces, Classes, Structs) for parent hierarchy determination
        active_scopes = []  # List of dicts: {"id","node","name","entity_type"}
        scope_lookup = {}

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen or node.id in processed_inner_specifiers:
                continue

            # Skip outer template_declaration wrappers; inner template bodies will be evaluated directly
            if node.type == "template_declaration":
                body=node.child_by_field_name(
                    "declaration"
                )
                if body:
                    node=body
                else:
                    continue

            name = ""
            entity_type = ""
            qualifier = ""

            # 1. Namespaces
            if node.type == "namespace_definition":
                name = self._extract_name(node)
                entity_type = "NAMESPACE"

            # 2. Typedefs and 'using' aliases
            elif node.type in ("type_definition", "alias_declaration"):
                name, entity_type, inner_specifier = self._extract_typedef_alias_info(node)
                if inner_specifier:
                    processed_inner_specifiers.add(inner_specifier.id)

            # 3. Classes, Structs, Enums, Unions
            elif node.type in ("class_specifier", "struct_specifier", "enum_specifier", "union_specifier"):
                # Ignore inline variable member references (e.g., struct Node *next)
                """
                ALLOWED_PARENTS = {
                        "translation_unit",
                        "declaration_list",
                        "namespace_definition",
                        "template_declaration",
                        "field_declaration"
                }
                if (node.parent is not None and node.parent.type not in ALLOWED_PARENTS):
                    continue
                """
                
                name = self._extract_name(node)
                if not name:
                    continue  # Ignore anonymous inner definitions without names

                type_map = {
                    "class_specifier": "CLASS",
                    "struct_specifier": "STRUCT",
                    "enum_specifier": "ENUM",
                    "union_specifier": "UNION",
                }
                entity_type = type_map[node.type]

            # 4. Functions & Methods
            elif node.type in ("function_definition", "declaration", "field_declaration"):
                if (
                    node.type=="field_declaration"
                    and
                    self._contains_type_definition(node)
                ):
                    continue

                if not self._is_function_declaration(
                        node
                    ):
                    continue

                name,qualifier,entity_type=(
                    self._extract_function_info(
                        node
                    )
                )

            if not name or not entity_type:
                continue

            seen.add(node.id)

            # --- Parent Resolution ---

            active_scopes = [
                s for s in active_scopes
                if (
                    s["node"].start_byte <= node.start_byte
                    and s["node"].end_byte >= node.end_byte
                )
            ]

            line = node.start_point[0] + 1
            column = node.start_point[1]

            # Qualified definitions
            # Example:
            # void Car::start()
            #
            if qualifier:

                parent_id = scope_lookup.get(
                    qualifier,
                    f"{self.file_path}#{qualifier}"
                )
                parent_type = (
                    "CLASS"
                    if "::" in qualifier or qualifier
                    else "FILE"
                )

            else:

                if active_scopes:
                    parent_id = (
                        active_scopes[-1]["id"]
                    )
                    parent_type = (
                        active_scopes[-1]["entity_type"]
                    )

                else:
                    parent_id = (
                        f"{self.file_path}#FILE"
                    )
                    parent_type = "FILE"

                # nested members become methods
                if (
                    active_scopes
                    and entity_type == "FUNCTION"
                ):
                    entity_type = "METHOD"

            base_id = (f"{parent_id}.{name}").replace("#.", "#")
            entity_id = (f"{base_id}:{line}:{column}")
            
            try:
                # If template wrapped, extract the outer signature
                working_node = node.parent if node.parent and node.parent.type == "template_declaration" else node

                working_node = (
                    node.parent
                    if (
                        node.parent
                        and node.parent.type
                        == "template_declaration"
                    )
                    else node
                )

                signature = self._get_signature(
                    working_node
                )

                impl_body = self._node_text(
                    working_node
                )

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": working_node.start_point[0] + 1,
                        "start_column": working_node.start_point[1],
                        "end_line": working_node.end_point[0] + 1,
                        "end_column": working_node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(working_node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push namespace, class, or struct into the scope stack for nested children
                if entity_type in ("NAMESPACE", "CLASS", "STRUCT"):
                    active_scopes.append(
                        {
                            "id": entity_id,
                            "node": node,
                            "name": name,
                            "entity_type": entity_type
                        }
                    )
                    scope_lookup[name] = entity_id

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture C++ script module if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* C++ Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class JavaHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures classes, interfaces, enums, records, and annotation declarations
    (class_declaration) @definition
    (interface_declaration) @definition
    (enum_declaration) @definition
    (record_declaration) @definition
    (annotation_type_declaration) @definition

    ;; Captures methods and constructors
    (method_declaration) @definition
    (constructor_declaration) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")
        
    def _get_signature(self,node):

        body=node.child_by_field_name("body")

        if body:
            text=self.file_content[
                node.start_byte:body.start_byte
            ].decode(
                "utf-8",
                errors="ignore"
            )
        else:
            text=self._node_text(node)

            if "{" in text:
                text=text.split("{",1)[0]

        return text.rstrip("{").strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type not in ("comment", "block_comment", "line_comment"):
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                # Clean up leading asterisks in Javadoc comments
                lines = text[2:-2].splitlines()
                cleaned_lines = []
                for line in lines:
                    line_str = line.strip()
                    if line_str.startswith("*"):
                        line_str = line_str[1:].strip()
                    cleaned_lines.append(line_str)
                text = "\n".join(cleaned_lines).strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_name(self, node: Node) -> str:
        name_node = node.child_by_field_name("name")
        if name_node:
            return self._node_text(name_node).strip()

        # Fallback for children search
        for child in node.children:
            if child.type == "identifier":
                return self._node_text(child).strip()

        return ""

    def parse(self, tree) -> list:
        lang = Language(tsjava.language())  # Adjust binding path if using tree_sitter_java
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #   print(child.type)
           
        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track scope hierarchy (Classes, Interfaces, Enums, Records, Annotation types)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen:
                continue

            name = self._extract_name(node)
            if not name:
                continue

            type_map = {
                "class_declaration": "CLASS",
                "interface_declaration": "INTERFACE",
                "enum_declaration": "ENUM",
                "record_declaration": "RECORD",
                "annotation_type_declaration": "ANNOTATION",
                "method_declaration": "METHOD",
                "constructor_declaration": "CONSTRUCTOR",
            }

            entity_type = type_map.get(node.type, "")
            if not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Determine Parent ID & Entity ID
            if active_scopes:
                #parent_id = f"{self.file_path}#{'.'.join([s['name'] for s in active_scopes])}".replace("#.", "#")
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"

            base_id = f"{parent_id}.{name}".replace("#.", "#")

            """
            # Append line offset to prevent collisions across overloaded methods/constructors
            if entity_type in ("METHOD", "CONSTRUCTOR"):
                entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            else:
                entity_id = base_id
            """ 
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push containers into scope stack for inner member parsing
                if entity_type in ("CLASS", "INTERFACE", "ENUM", "RECORD", "ANNOTATION"):
                    active_scopes.append({"id":entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture Java file/module if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* Java Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class RustHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures structs, enums, traits, unions, and type aliases
    (struct_item) @definition
    (enum_item) @definition
    (trait_item) @definition
    (union_item) @definition
    (type_item) @definition

    ;; Captures inline module definitions
    (mod_item) @definition

    ;; Captures impl blocks (for structural context tracking)
    (impl_item) @definition

    ;; Captures functions / methods
    (function_item) @definition
    (function_signature_item) @definition
    
    (macro_definition) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode("utf-8", errors="ignore").strip()

        text = self._node_text(node)
        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type not in ("comment", "line_comment", "block_comment"):
                break
            text = self._node_text(sibling)
            if text.startswith("///") or text.startswith("//!"):
                text = text[3:].strip()
            elif text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                lines = text[2:-2].splitlines()
                cleaned_lines = [l.strip().lstrip("*").strip() for l in lines]
                text = "\n".join(cleaned_lines).strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_name(self, node: Node) -> str:
        name_node = node.child_by_field_name("name")
        if name_node:
            return self._node_text(name_node).strip()

        for child in node.children:
            if child.type == "identifier":
                return self._node_text(child).strip()

        return ""

    def _extract_impl_target(self, node: Node) -> tuple[str, str]:
        """Extract target type or trait from an impl block (e.g. impl Foo or impl Trait for Foo)."""
        type_node = node.child_by_field_name("type")
        trait_node = node.child_by_field_name("trait")

        def clean_type_name(t_node: Node) -> str:
            if not t_node:
                return ""
            if t_node.type == "generic_type":
                inner_type = t_node.child_by_field_name("type") or next(
                    (c for c in t_node.children if c.type == "type_identifier"), None
                )
                if inner_type:
                    return self._node_text(inner_type).strip()
            
            raw_name = self._node_text(t_node).strip()
            if "<" in raw_name:
                raw_name = raw_name.split("<")[0].strip()
            return raw_name

        target_type = clean_type_name(type_node) or "anonymous_impl"

        if trait_node:
            trait_name = clean_type_name(trait_node)
            return target_type, f"IMPL_{trait_name}"

        return target_type, "STRUCT"

    def parse(self, tree) -> list:
        lang = Language(tsrust.language())  # Adjust binding call to match your loader
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()
        
        # Track structural scope stack (Modules, Structs, Traits, Impl Blocks)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}
        
        self.type_lookup = {}
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #   print(child.type)

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen:
                continue

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # 1. Handle `impl` Blocks (Container context tracking only)
            if node.type == "impl_item":
                impl_name, impl_type = self._extract_impl_target(node)
                parent_id=(
                    self.type_lookup.get(
                        impl_name,
                        f"{self.file_path}#FILE"
                    )
                )
                active_scopes.append({"id":parent_id,"node": node, "name": impl_name, "entity_type": impl_type})
                continue

            name = self._extract_name(node)
            if not name:
                continue

            type_map = {
                "struct_item": "STRUCT",
                "enum_item": "ENUM",
                "trait_item": "TRAIT",
                "union_item": "UNION",
                "type_item": "TYPE_ALIAS",
                "mod_item": "MODULE",
                "function_item": "FUNCTION",
                "function_signature_item":"FUNCTION",
                "macro_definition":"MACRO",
            }

            entity_type = type_map.get(node.type, "")
            if not entity_type:
                continue

            # Categorize functions inside an impl or trait block as METHOD
            if entity_type == "FUNCTION" and active_scopes:
                parent_type = active_scopes[-1]["entity_type"]
                if parent_type in ("STRUCT", "ENUM", "TRAIT", "UNION") or parent_type.startswith("IMPL_"):
                    entity_type = "METHOD"

            seen.add(node.id)

            # Determine Parent ID & Entity ID
            if active_scopes:
                #parent_id = f"{self.file_path}#{'.'.join([s['name'] for s in active_scopes])}".replace("#.", "#")
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"

            base_id = f"{parent_id}.{name}".replace("#.", "#")

            """
            if entity_type in ("FUNCTION", "METHOD"):
                entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            else:
                entity_id = base_id
            """ 
                
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push structural containers into active_scopes
                if entity_type in ("STRUCT", "ENUM", "TRAIT", "UNION", "MODULE"):
                    self.type_lookup[(name)] = entity_id
                    active_scopes.append({"id":entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture Rust script module if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "// Rust Source Module",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class TSHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; namespaces/modules
    (internal_module) @definition
    
    ;; Captures classes, interfaces, enums, and type aliases
    (class_declaration) @definition
    (interface_declaration) @definition
    (enum_declaration) @definition
    (type_alias_declaration) @definition

    ;; Captures traditional function declarations
    (function_declaration) @definition

    ;; Captures method definitions inside classes or object literals
    (method_definition) @definition
    (method_signature) @definition

    ;; Captures lexical variable declarations (const/let bound arrow functions & function expressions)
    (lexical_declaration) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode("utf-8", errors="ignore").strip()

        text = self._node_text(node)
        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type not in ("comment", "line_comment", "block_comment"):
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                lines = text[2:-2].splitlines()
                cleaned_lines = [l.strip().lstrip("*").strip() for l in lines]
                text = "\n".join(cleaned_lines).strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extract name and entity type for TypeScript AST nodes."""
        node_type = node.type
        
        if node_type == "internal_module":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "MODULE"

        if node_type == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "CLASS"

        if node_type == "interface_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "INTERFACE"

        if node_type == "enum_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "ENUM"

        if node_type == "type_alias_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "TYPE_ALIAS"

        if node_type == "function_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "FUNCTION"

        if node_type in ("method_definition", "method_signature"):
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "METHOD"

        if node_type == "lexical_declaration":
            # Search for const fn = () => {} or const fn = function() {}
            for child in node.children:
                if child.type == "variable_declarator":
                    val_node = child.child_by_field_name("value")
                    if val_node and val_node.type in ("arrow_function", "function_expression"):
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            return self._node_text(name_node).strip(), "FUNCTION"

        return "", ""

    def parse(self, tree) -> list:
        lang = Language(tsts.language_typescript())  # Adjust binding call if using tree_sitter_typescript
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #   print(child.type)
        
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track structural scopes (Classes, Interfaces, Enums)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen:
                continue

            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Determine Parent ID & Entity ID
            if active_scopes:
                #parent_id = f"{self.file_path}#{'.'.join([s['name'] for s in active_scopes])}".replace("#.", "#")
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"

            base_id = f"{parent_id}.{name}".replace("#.", "#")

            """
            # Append line offset to functions/methods to disambiguate overloads and variable bindings
            if entity_type in ("FUNCTION", "METHOD"):
                entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            else:
                entity_id = base_id
            """
            
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            
            try:
                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push structural containers into active_scopes for inner member nesting
                if entity_type in ("MODULE", "CLASS", "INTERFACE", "ENUM", "METHOD", "FUNCTION"):
                    active_scopes.append({"id": entity_id,"node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture TS script module if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "// TypeScript Source Module",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class JSHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures ES6 classes
    (class_declaration) @definition

    ;; Captures traditional function declarations
    (function_declaration) @definition

    ;; Captures method definitions inside classes or object literals
    (method_definition) @definition

    ;; Captures lexical variable declarations (const/let/var bound arrow functions & function expressions)
    (lexical_declaration) @definition
    (variable_declaration) @definition
    
    (expression_statement) @definition
    
    (field_definition) @definition
    
    (pair) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        body = node.child_by_field_name("body")
        if body:
            return self.file_content[
                node.start_byte:body.start_byte
            ].decode("utf-8", errors="ignore").strip()

        text = self._node_text(node)
        if "{" in text:
            return text.split("{", 1)[0].strip()

        return text.strip()

    def _get_preceding_comments(self, node: Node) -> str:
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type not in ("comment", "line_comment", "block_comment"):
                break
            text = self._node_text(sibling)
            if text.startswith("//"):
                text = text[2:].strip()
            elif text.startswith("/*") and text.endswith("*/"):
                lines = text[2:-2].splitlines()
                cleaned_lines = [l.strip().lstrip("*").strip() for l in lines]
                text = "\n".join(cleaned_lines).strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extract name and entity type for JavaScript AST nodes."""
        node_type = node.type

        if node_type == "class_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "CLASS"

        if node_type == "function_declaration":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "FUNCTION"

        if node_type == "method_definition":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "METHOD"

        if node_type in ("lexical_declaration","variable_declaration"):
            for child in node.children:
                if child.type!="variable_declarator":
                    continue
                value = child.child_by_field_name("value")
                name = child.child_by_field_name("name")
                if not value or not name:
                    continue
                name = self._node_text(name).strip()
                # functions
                if value.type in (
                    "arrow_function",
                    "function_expression"
                ):
                    return name,"FUNCTION"
                # classes
                if value.type=="class":
                    return name,"CLASS"
                # objects
                if value.type=="object":
                    return name,"MODULE"
                
        if node_type=="expression_statement":

            expr=None

            for child in node.children:
                if child.type=="assignment_expression":
                    expr=child
                    break

            if not expr:
                return "",""

            left=expr.child_by_field_name("left")
            right=expr.child_by_field_name("right")

            if not left or not right:
                return "",""

            name=self._node_text(left).strip()

            if right.type=="class":
                return name,"CLASS"

            if right.type=="object":
                return name,"MODULE"

            if "function" in right.type or "arrow" in right.type:
                return name,"FUNCTION"

            return "",""
        
        if node_type=="field_definition":

            value=None
            name=None

            for child in node.children:

                if child.type in (
                    "class",
                    "object",
                    "function_expression",
                    "arrow_function"
                ):
                    value=child

                elif child.type=="property_identifier":
                    name=child

            if not value or not name:
                return "",""

            name=self._node_text(name).strip()

            if value.type=="class":
                return name,"CLASS"

            if value.type=="object":
                return name,"MODULE"

            if "function" in value.type or "arrow" in value.type:
                return name,"FUNCTION"
                
        if node_type == "pair":

            key = node.child_by_field_name("key")
            value = node.child_by_field_name("value")
            if not key or not value:
                return "", ""
            name = self._node_text(key).strip()
            # Nested object literals
            # Sorting : { ... }
            if value.type == "object":
                return name, "MODULE"
            # Nested classes
            # Parser : class { ... }
            if value.type == "class":
                return name, "CLASS"
            if "function" in value.type or "arrow" in value.type:
                return name,"FUNCTION"

            return "", ""

        return "", ""
    
    def _get_syntactic_parent(self,node):

        current=node.parent

        while current:

            if current.id in self.scope_lookup:
                return self.scope_lookup[current.id]

            current=current.parent

        return None

    def _is_structural_scope(self,node,entity_type):
        return entity_type in (
            "CLASS",
            "FUNCTION",
            "MODULE"
        )


    def parse(self, tree) -> list:
        lang = Language(tsjs.language())  # Adjust binding call if using tree_sitter_javascript
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #           print(child.type)
                   
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        self.scope_lookup = {}
        seen = set()

        # Track structural scopes (Classes)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}

        for _, _, captures in ordered:
            node = captures["definition"][0]
            #print(node.type,self._node_text(node))

            if node.id in seen:
                continue

            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Determine Parent ID & Entity IDs

            parent_info = self._get_syntactic_parent(node)

            if parent_info:
                parent_id, parent_type = parent_info

            elif active_scopes:
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]

            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"

            
            base_id = f"{parent_id}.{name}".replace("#.", "#")

            """
            # Append line offset to functions/methods to disambiguate collisions across closures/re-declarations
            if entity_type in ("FUNCTION", "METHOD"):
                entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            else:
                entity_id = base_id
            """
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            
            try:

                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push structural containers into active_scopes for inner method nesting
                if self._is_structural_scope(node, entity_type):

                    self.scope_lookup[node.id] = (
                        entity_id,
                        entity_type
                    )

                    # const X = ....
                    if node.type in (
                        "lexical_declaration",
                        "variable_declaration"
                    ):
                        for child in node.children:
                            if child.type == "variable_declarator":
                                self.scope_lookup[child.id] = (
                                    entity_id,
                                    entity_type
                                )

                    # X : ....
                    elif node.type == "pair":
                        value = node.child_by_field_name("value")
                        if value:
                            self.scope_lookup[value.id] = (
                                entity_id,
                                entity_type
                            )

                    # X = ....
                    elif node.type == "expression_statement":
                        if len(node.children) == 1:
                            expr = node.children[0]
                            if expr.type == "assignment_expression":
                                self.scope_lookup[expr.id] = (
                                    entity_id,
                                    entity_type
                                )

                                right = expr.child_by_field_name("right")
                                if right:
                                    self.scope_lookup[right.id] = (
                                        entity_id,
                                        entity_type
                                    )

                    # static X = class {...}
                    elif node.type == "field_definition":
                        for child in node.children:
                            if child.type in ("class", "object"):
                                self.scope_lookup[child.id] = (
                                    entity_id,
                                    entity_type
                                )

                    active_scopes.append(
                        {
                            "id": entity_id,
                            "node": node,
                            "name": name,
                            "entity_type": entity_type,
                        }
                    )

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture JS script module if no entities were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "// JavaScript Source Module",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class RubyHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures Ruby modules
    (module) @definition

    ;; Captures Ruby classes
    (class) @definition
    
    ;; Captures Struct.new(), Class.new() etc.
    (assignment
        left:(constant)
        right:(call)
    ) @definition

    ;; Captures instance and class/singleton methods
    (method) @definition
    (singleton_method) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_signature(self, node: Node) -> str:
        """Constructs a clean method or class signature."""
        node_type = node.type

        if node_type == "class":
            name_node = node.child_by_field_name("name")
            superclass_node = node.child_by_field_name("superclass")
            name = self._node_text(name_node).strip() if name_node else ""
            if superclass_node:
                superclass = self._node_text(superclass_node).strip()
                return f"class {name} < {superclass}"
            return f"class {name}"

        if node_type == "module":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return f"module {name}"
        
        if node_type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left and right:
                name = self._node_text(left).strip()
                receiver = right.child_by_field_name("receiver")
                method = right.child_by_field_name("method")
                if receiver and method:
                    receiver_name = self._node_text(receiver).strip()
                    method_name = self._node_text(method).strip()
                    return f"class {name} ({receiver_name}.{method_name})"

            return self._node_text(node).strip()

        if node_type in ("method", "singleton_method"):
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")

            name = self._node_text(name_node).strip() if name_node else ""
            params = f" {self._node_text(params_node).strip()}" if params_node else ""

            prefix = "def self." if node_type == "singleton_method" else "def "
            return f"{prefix}{name}{params}"

        return self._node_text(node).split("\n", 1)[0].strip()

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding RDoc or YARD comments attached to the node."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type != "comment":
                break
            text = self._node_text(sibling).strip()
            if text.startswith("#"):
                text = text.lstrip("#").strip()
            comments.append(text)

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extracts the entity name and unified type for Ruby nodes."""
        node_type = node.type

        if node_type == "module":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "MODULE"

        if node_type == "class":
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "CLASS"

        if node_type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if not left or not right:
                return "", ""
            # We only care about constant assignments.
            if left.type != "constant":
                return "", ""
            # Ignore everything except calls.
            if right.type != "call":
                return "", ""
            receiver = right.child_by_field_name("receiver")
            method = right.child_by_field_name("method")
            if not receiver or not method:
                return "", ""
            receiver_name = self._node_text(receiver).strip()
            method_name = self._node_text(method).strip()
            # Support:
            # Person = Struct.new(...)
            # User = Class.new(...)
            # Coordinates = Data.define(...)
            # Treat these as class definitions.
            class_like_assignments = {
                ("Struct", "new"),
                ("Class", "new"),
                ("Data", "define"),
            }

            if (receiver_name, method_name) in class_like_assignments:
                name = self._node_text(left).strip()
                return name, "CLASS"

            return "", ""

        if node_type in ("method", "singleton_method"):
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node).strip() if name_node else ""
            return name, "METHOD"

        return "", ""

    def parse(self, tree) -> list:
        lang = Language(tsruby.language())  # Adjust binding if using alternative tree-sitter package
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #           print(child.type)
        
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track structural scopes (Modules & Classes)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}

        for _, _, captures in ordered:
            node = captures["definition"][0]

            if node.id in seen:
                continue

            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Determine Parent ID & Entity ID with namespace propagation
            if active_scopes:
                #parent_id = f"{self.file_path}#{'.'.join([s['name'] for s in active_scopes])}".replace("#.", "#")
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"

            base_id = f"{parent_id}.{name}".replace("#.", "#")

            """
            # Append line offset to methods to disambiguate collisions across re-definitions
            if entity_type == "METHOD":
                entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            else:
                entity_id = base_id
            """
            
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"
            
            try:
                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push structural containers into active_scopes for nested class/module/method tracking
                if entity_type in ("CLASS", "MODULE"):
                    active_scopes.append({"id": entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture Ruby script module if no classes, modules, or methods were matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "# Ruby Source Module",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve source code line order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #   print(e["entity_type"], e["name"])

        return self.entities

class CSSHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures CSS rulesets
    (rule_set) @definition

    ;; Captures at-rules (media, supports, keyframes, containers)
    (media_statement) @definition
    (supports_statement) @definition
    (keyframes_statement) @definition
    
    (at_rule) @definition
    """

    def _node_text(self, node: Node) -> str:
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _find_selector_text(self, node: Node) -> str:
        """Extracts clean selector text from a rule_set node."""
        for child in node.children:
            if child.type in ("selectors", "selector"):
                return self._node_text(child).strip()
        
        # Fallback: grab everything up to the block opening brace `{`
        first_line = self._node_text(node).split("{", 1)[0].strip()
        return first_line if first_line else "unknown-selector"

    def _get_signature(self, node: Node) -> str:
        """Constructs a signature for rulesets and at-rules."""
        node_type = node.type

        if node_type == "rule_set":
            return self._find_selector_text(node)

        if node_type in ("media_statement", "supports_statement", "keyframes_statement", "at_rule"):
            signature = self._node_text(node).split("{", 1)[0].strip()
            if not signature:
                rule_name = node_type.split("_")[0]
                return f"@{rule_name}"
            return signature

        return self._node_text(node).split("\n", 1)[0].strip()

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding CSS comments (/* ... */) directly above a statement."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type != "comment":
                break
            
            raw_text = self._node_text(sibling).strip()
            if raw_text.startswith("/*") and raw_text.endswith("*/"):
                clean_lines = []
                for line in raw_text.split("\n"):
                    cleaned = line.strip().lstrip("/*").rstrip("*/").strip()
                    if cleaned:
                        clean_lines.append(cleaned)
                comments.append("\n".join(clean_lines))

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extracts entity name and category for CSS AST nodes."""
        node_type = node.type

        if node_type == "rule_set":
            selector = self._find_selector_text(node)
            return selector, "RULESET"

        if node_type in ("media_statement", "supports_statement", "keyframes_statement", "at_rule"):
            header = self._get_signature(node)
            return header, "AT_RULE"

        return "", ""

    def _should_capture_at_rule(self, node: Node) -> bool:
        """
        Captures only selected generic CSS at-rules.
        """
        signature = self._get_signature(node)

        # here can extend the list if wanted
        supported_at_rules = (
            "@container",
            "@layer",
            "@import",
            "@charset",
        )

        return any(
            signature.startswith(rule)
            for rule in supported_at_rules
        )
        
    
    def parse(self, tree) -> list:
        lang = Language(tscss.language())  # Adjust binding if using alternative tree-sitter package
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track container scopes (e.g., @media queries or @supports blocks)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str}

        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #           print(child.type)

        for _, _, captures in ordered:
            node = captures["definition"][0]
            if node.id in seen:
                continue
            # Capture only selected generic at-rules.
            if node.type == "at_rule":
                if not self._should_capture_at_rule(node):
                    continue
            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue
    
            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Resolve Parent ID & Entity ID
            if active_scopes:
                #parent_id = f"{self.file_path}#{' -> '.join([s['name'] for s in active_scopes])}"
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
                #base_id = f"{parent_id}.{name}"
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"
                #base_id = f"{self.file_path}#{name}"
                
            base_id = f"{parent_id}.{name}".replace("#.", "#")

            # Append line offset to avoid duplicate key issues across identical selector blocks
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                signature = self._get_signature(node)
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push media/supports containers to active_scopes for nested rule tracking
                if entity_type == "AT_RULE":
                    active_scopes.append({"id": entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture CSS file as a script entity if no rulesets/at-rules matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* CSS Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve line order in stylesheet
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )

        #for e in self.entities:
        #  print(e["entity_type"], e["name"])
        
        return self.entities

class HTMLHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures HTML elements (standard, script, style, form elements)
    (element) @definition
    (script_element) @definition
    (style_element) @definition
    """

    TARGET_TAGS = {
        # Document Structure
        "body", "header", "footer", "main", "nav", "section", "article", "aside", "head",
        # Generic Containers
        "div", "template", "dialog",
        # Forms
        "form",
        # Media & Graphics
        "figure", "picture", "audio", "video", "canvas", "svg",
        # Data & Interactive Components
        "table", "details",
        # Embedded Content
        "script", "style", "noscript",
        "button","ul",
    }

    def _node_text(self, node: Node) -> str:
        """Extracts UTF-8 text for a node."""
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_tag_name(self, node: Node) -> str:
        """Safely extracts the HTML tag name from an element node."""
        for child in node.children:
            if child.type in ("start_tag", "script_start_tag", "style_start_tag"):
                for sub_child in child.children:
                    if sub_child.type == "tag_name":
                        return self._node_text(sub_child).strip().lower()
        return ""

    def _build_signature(self, node: Node, tag_name: str) -> str:
        """Constructs a descriptive selector-like signature using id and class attributes."""
        if not tag_name:
            return "unknown-element"

        # Find the start tag node to grab its raw text
        start_tag_text = ""
        for child in node.children:
            if child.type in ("start_tag", "script_start_tag", "style_start_tag"):
                start_tag_text = self._node_text(child)
                break

        element_id = ""
        element_classes = []

        if start_tag_text:
            # Extract id="..." or id='...'
            id_match = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', start_tag_text, re.IGNORECASE)
            if id_match:
                element_id = f"#{id_match.group(1).strip()}"

            # Extract class="..." or class='...'
            class_match = re.search(r'\bclass\s*=\s*["\']([^"\']+)["\']', start_tag_text, re.IGNORECASE)
            if class_match:
                classes = class_match.group(1).split()
                element_classes = [f".{cls.strip()}" for cls in classes if cls.strip()]

        class_suffix = "".join(element_classes)
        return f"<{tag_name}{element_id}{class_suffix}>"

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding HTML comments (<!-- ... -->) directly above an element."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            # Skip empty text nodes, whitespaces, or newlines between comments and element
            if sibling.type in ("text", "whitespace") and not self._node_text(sibling).strip():
                continue

            if sibling.type != "comment":
                break

            raw_text = self._node_text(sibling).strip()
            if raw_text.startswith("<!--") and raw_text.endswith("-->"):
                clean_lines = []
                for line in raw_text.split("\n"):
                    cleaned = line.strip().lstrip("<!--").rstrip("-->").strip()
                    if cleaned:
                        clean_lines.append(cleaned)
                comments.append("\n".join(clean_lines))

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extracts entity name and category for HTML AST nodes."""
        tag_name = self._get_tag_name(node)
        if tag_name in self.TARGET_TAGS:
            signature = self._build_signature(node, tag_name)
            return signature, "HTML_ELEMENT"

        return "", ""

    def parse(self, tree) -> list:
        lang = Language(tshtmls.language())  # Adjust binding as per installed tree-sitter html package
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track container scopes (e.g., nested target HTML elements)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str, "id": str}
        
        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #        print(child.type)

        for _, _, captures in ordered:
            node = captures["definition"][0]
            if node.id in seen:
                continue

            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Resolve Parent ID & Entity ID
            if active_scopes:
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
                #base_id = f"{parent_id} -> {name}"
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"
                #base_id = f"{self.file_path}#{name}"

            base_id = f"{parent_id}.{name}".replace("#.", "#")
            # Append line:column offset to prevent ID collision across identical signatures
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                signature = name  # Name is already formatted as <tag#id.class>
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push container element to active_scopes for nested element tracking
                active_scopes.append({"id": entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture HTML file as a script/module entity if no target elements matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "<!-- HTML Source Module -->",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve line order in HTML document
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #  print(e["entity_type"], e["name"])

        return self.entities

class JSONHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures JSON objects and arrays
    (object) @definition
    (array) @definition
    """

    def _node_text(self, node: Node) -> str:
        """Extracts UTF-8 text for a node."""
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_key_for_node(self, node: Node) -> str:
        """
        Finds the object key name assigned to this node, if it sits inside a pair.
        e.g., if parent is 'pair' with key 'metadata', returns 'metadata'.
        """
        parent = node.parent
        if parent and parent.type == "pair":
            for child in parent.children:
                if child.type in ("string", "key"):
                    raw_text = self._node_text(child).strip()
                    return raw_text.strip('"\'')
        return ""

    def _get_index_for_array_item(self, node: Node) -> str:
        """
        If this node is inside an array, find its zero-based index position.
        e.g., returns '[0]', '[1]', etc.
        """
        parent = node.parent
        if parent and parent.type == "array":
            idx = 0
            for child in parent.children:
                if child.id == node.id:
                    return f"[{idx}]"
                # Increment index for value nodes, ignoring punctuation like commas/brackets
                if child.type in ("object", "array", "string", "number", "true", "false", "null"):
                    idx += 1
        return ""

    def _build_signature(self, node: Node) -> str:
        """
        Builds a clean, scannable signature for the structural block.
        Examples: "{metadata}", "[services]", "{services[0]}", "{root}"
        """
        is_obj = node.type == "object"
        symbol_open = "{" if is_obj else "["
        symbol_close = "}" if is_obj else "]"

        # Case 1: Assigned to an object key
        key = self._get_key_for_node(node)
        if key:
            return f"{symbol_open}{key}{symbol_close}"

        # Case 2: Element in an array
        idx_suffix = self._get_index_for_array_item(node)
        if idx_suffix:
            array_key = self._get_key_for_node(node.parent) if node.parent else ""
            if array_key:
                return f"{symbol_open}{array_key}{idx_suffix}{symbol_close}"
            return f"{symbol_open}item{idx_suffix}{symbol_close}"

        # Root block fallback
        return f"{symbol_open}root{symbol_close}"

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding comments if parsing JSONC (JSON with comments)."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type in ("comment", "line_comment", "block_comment"):
                raw_text = self._node_text(sibling).strip()
                comments.append(raw_text)
            elif sibling.type not in ("text", "whitespace"):
                break

        comments.reverse()
        return "\n".join(comments)

    def _extract_name_and_type(self, node: Node):
        """Extracts entity name and category for JSON AST nodes."""
        if node.type in ("object", "array"):
            signature = self._build_signature(node)
            return signature, "JSON_STRUCTURE"
        return "", ""

    def parse(self, tree) -> list:
        lang = Language(tsjson.language())  # Adjust binding as per installed tree-sitter json package
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        seen = set()

        # Track container scopes (nested objects or arrays)
        active_scopes = []  # List of dicts: {"node": Node, "name": str, "entity_type": str, "id": str}

        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #        print(child.type)
        
        for _, _, captures in ordered:
            node = captures["definition"][0]
            if node.id in seen:
                continue

            name, entity_type = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # Maintain active scope stack based on byte range containment
            active_scopes = [
                s for s in active_scopes
                if s["node"].start_byte <= node.start_byte and s["node"].end_byte >= node.end_byte
            ]

            # Resolve Parent ID & Entity ID
            if active_scopes:
                parent_id = active_scopes[-1]["id"]
                parent_type = active_scopes[-1]["entity_type"]
                #base_id = f"{parent_id} -> {name}"
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"
                #base_id = f"{self.file_path}#{name}"

            base_id = f"{parent_id}.{name}".replace("#.", "#")

            # Append line:column offset to prevent ID collision across identical key names
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                signature = name
                impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push container to active_scopes for nested tracking
                active_scopes.append({"id": entity_id, "node": node, "name": name, "entity_type": entity_type})

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture JSON file as a single module entity if no objects/arrays matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name": "module",
                    "entity_type": "SCRIPT",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature": "/* JSON Source Module */",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve line order in JSON file
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #  print(e["entity_type"], e["name"])

        return self.entities

class MarkdownHandler(BaseLanguageHandler):

    QUERY_SOURCE = r"""
    ;; Captures ATX and Setext headings
    (atx_heading) @definition
    (setext_heading) @definition

    ;; Captures fenced code blocks
    (fenced_code_block) @definition
    
    (pipe_table) @definition
    (html_block) @definition
    """

    def _node_text(self, node: Node) -> str:
        """Extracts UTF-8 text for a node."""
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _get_heading_level_and_text(self, node: Node) -> tuple:
        """
        Extracts the heading depth level (e.g., H1 -> 1, H2 -> 2)
        and the clean text string of the heading.
        """
        level = 1
        text = ""

        if node.type == "atx_heading":
            for child in node.children:
                if child.type.startswith("atx_h") and child.type.endswith("_marker"):
                    # Extract level integer from marker (e.g. atx_h2_marker -> 2)
                    try:
                        level = int(child.type[5])
                    except (IndexError, ValueError):
                        level = 1
                elif child.type == "heading_content":
                    text = self._node_text(child).strip()

        elif node.type == "setext_heading":
            for child in node.children:
                if child.type == "paragraph":
                    text = self._node_text(child).strip()
                elif child.type == "setext_h1_marker":
                    level = 1
                elif child.type == "setext_h2_marker":
                    level = 2

        # Fallback if content was not isolated cleanly
        if not text:
            text = self._node_text(node).strip().lstrip("#").strip()

        return level, text

    def _get_code_block_lang(self, node: Node) -> str:
        """Retrieves the language label defined in the fenced code block (e.g. 'sql', 'python')."""
        for child in node.children:
            if child.type == "info_string":
                for sub_child in child.children:
                    if sub_child.type == "language":
                        return self._node_text(sub_child).strip()
        return "text"

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding HTML comments or doc-style blockquotes directly above a node."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type in ("text", "whitespace") and not self._node_text(sibling).strip():
                continue

            if sibling.type == "html_block":
                raw_text = self._node_text(sibling).strip()
                if raw_text.startswith("<!--") and raw_text.endswith("-->"):
                    clean_lines = [
                        line.strip().lstrip("<!--").rstrip("-->").strip()
                        for line in raw_text.split("\n")
                        if line.strip()
                    ]
                    comments.append("\n".join(clean_lines))
                else:
                    break
            else:
                break

        comments.reverse()
        return "\n".join(comments)
    
    def _get_html_block_name(self, node):
        text = self._node_text(node).strip()
        if text.startswith("<!--"):
            return "html-comment"
        if text.startswith("<details"):
            return "html-details"
        if text.startswith("<div"):
            return "html-div"
        if text.startswith("<table"):
            return "html-table"
        return "html-block"
    
    def _get_table_name(self, node: Node) -> str:
        """
        Extracts a meaningful name for a Markdown table from its header row. Falls back to 'table' if no header could be determined.
        """
        text = self._node_text(node).strip()
        if not text:
            return "table"
        try:
            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]
            # Markdown tables require at least 2 lines
            if len(lines) < 2:
                return "table"
            header = lines[0]
            # Remove leading/trailing pipes
            if header.startswith("|"):
                header = header[1:]
            if header.endswith("|"):
                header = header[:-1]
            columns = [
                column.strip().lower()
                for column in header.split("|")
                if column.strip()
            ]
            if not columns:
                return "table"
            # Keep names reasonably short.
            # Example:
            # table-name-age-country
            name = "-".join(columns[:4])
            return f"table-{name}"
        except Exception:
            return "table"

    def _get_heading_section_body(self, node: Node, level: int, ordered_nodes: list[Node],) -> str:
        """
        Returns the complete markdown section belonging to a heading.
        A heading owns everything from its start position until the next heading having level <= its own level.
        """

        start_byte = node.start_byte
        end_byte = len(self.file_content)
        found_current = False
        for next_node in ordered_nodes:
            if next_node.id == node.id:
                found_current = True
                continue
            if not found_current:
                continue
            # Only headings can terminate a section
            if next_node.type in ("atx_heading", "setext_heading"):
                next_level, _ = self._get_heading_level_and_text(next_node)
                # Stop when we encounter a heading having
                # level <= current heading level.
                if next_level <= level:
                    end_byte = next_node.start_byte
                    break
        return self.file_content[
            start_byte:end_byte
        ].decode("utf-8", errors="ignore").rstrip()
    
    def _extract_name_and_type(self, node: Node):
        """Extracts entity name, signature, and category for Markdown AST nodes."""
        if node.type in ("atx_heading", "setext_heading"):
            level, text = self._get_heading_level_and_text(node)
            return text, f"H{level}: {text}", "MARKDOWN_HEADING", level

        if node.type == "fenced_code_block":
            lang = self._get_code_block_lang(node)
            return (
                f"codeblock-{lang}",
                f"CODE_BLOCK<{lang}>",
                "MARKDOWN_CODE_BLOCK",
                None
            )
        
        if node.type == "html_block":
            name = self._get_html_block_name(node)
            return (
                name,
                name,
                "MARKDOWN_HTML_BLOCK",
                None
            )

        if node.type == "pipe_table":
            name = self._get_table_name(node)
            return (
                name,
                "MARKDOWN_TABLE",
                "MARKDOWN_TABLE",
                None
            )

        return "", "", "", None

    def parse(self, tree) -> list:
        lang = Language(tsmarkdown.language())  # Adjust binding as per installed tree-sitter markdown package
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        ordered_nodes = []

        for _, _, capture_dict in ordered:
            defs = capture_dict.get("definition", [])

            if defs:
                ordered_nodes.append(defs[0])
                
        self.entities = []
        seen = set()

        # Track active heading context stack: list of dicts {"id": str, "level": int, "name": str}
        heading_stack = []

        print("has_error:", tree.root_node.has_error)
        #for child in tree.root_node.children:
        #        print(child.type)
        
        for _, _, captures in ordered:
            node = captures["definition"][0]
            if node.id in seen:
                continue

            name, signature, entity_type, level = self._extract_name_and_type(node)
            if not name or not entity_type:
                continue

            seen.add(node.id)

            # If node is a heading, collapse deeper or equal headings from stack
            if entity_type == "MARKDOWN_HEADING" and level is not None:
                while heading_stack and heading_stack[-1]["level"] >= level:
                    heading_stack.pop()

            # Resolve parent scope based on active heading stack
            if heading_stack:
                parent_info = heading_stack[-1]
                parent_id = parent_info["id"]
                parent_type = "MARKDOWN_HEADING"
                #base_id = f"{parent_id} -> {name}"
            else:
                parent_id = f"{self.file_path}#FILE"
                parent_type = "FILE"
                #base_id = f"{self.file_path}#{name}"
                
            base_id = f"{parent_id}.{name}".replace("#.", "#")

            # Append line:column offset to prevent ID collision across duplicate section names
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            try:
                if entity_type == "MARKDOWN_HEADING":
                    impl_body = self._get_heading_section_body(node, level, ordered_nodes,)
                else:
                    impl_body = self._node_text(node)

                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": entity_type,
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": impl_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)

                # Push heading to stack to serve as parent scope for subsequent nodes
                if entity_type == "MARKDOWN_HEADING" and level is not None:
                    heading_stack.append({
                        "id": entity_id,
                        "level": level,
                        "name": name
                    })

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback: Capture Markdown file as a single module entity if no headings or code blocks matched
        if not self.entities and tree.root_node:
            raw_text = self._node_text(tree.root_node).strip()
            if raw_text:
                self.entities.append({
                    "id": f"{self.file_path}#SCRIPT",
                    "name":"markdown-module",
                    "entity_type":"MARKDOWN_MODULE",
                    "scope_range": {
                        "start_line": tree.root_node.start_point[0] + 1,
                        "start_column": tree.root_node.start_point[1],
                        "end_line": tree.root_node.end_point[0] + 1,
                        "end_column": tree.root_node.end_point[1],
                    },
                    "signature":"MARKDOWN_MODULE",
                    "implementation_body": raw_text,
                    "associated_docstring": "",
                    "parent_id": f"{self.file_path}#FILE",
                    "parent_type": "FILE",
                })

        # Preserve line order in Markdown document
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )

        #for e in self.entities:
        #    print(e["entity_type"], e["name"])
            
        return self.entities    

class SQLHandler(BaseLanguageHandler):

    # Generic top-level statement capture valid across tree-sitter-sql grammars
    QUERY_SOURCE = r"""
    (statement) @definition
    """

    def _node_text(self, node: Node) -> str:
        """Extracts UTF-8 text for a node."""
        return self.file_content[
            node.start_byte:node.end_byte
        ].decode("utf-8", errors="ignore")

    def _determine_statement_action(self, node_text: str) -> str:
        """Deduces the action keyword (SELECT, CREATE, etc.) from the raw query start."""
        cleaned = node_text.strip().upper()
        for action in ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH"]:
            if cleaned.startswith(action):
                return action
        return "STATEMENT"

    def _extract_primary_entity_name(self, node: Node) -> tuple[str, str | None]:
        """
        Extracts the primary target object from a SQL statement.
        Returns (object_name, object_type).

        Examples:
            ("auth.users", "TABLE")
            ("idx_users_email", "INDEX")
            ("projects", None)
            ("project_summary", None)
        """

        raw_text = self._node_text(node).strip()

        #print("=" * 80)
        #print(raw_text)
        #print("=" * 80)

        upper_text = raw_text.upper()

        def clean_name(name):
            return (
                name.replace("`", "")
                .replace('"', "")
                .replace("'", "")
                .rstrip(";")
            )

        if upper_text.startswith("CREATE"):

            patterns = [

                (r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w\.]+)", "TABLE"),
                (r"\bCREATE\s+VIEW\s+([\w\.]+)", "VIEW"),
                (r"\bCREATE\s+MATERIALIZED\s+VIEW\s+([\w\.]+)", "MATERIALIZED_VIEW"),
                (r"\bCREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\s+([\w\.]+)", "FUNCTION"),
                (r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+([\w\.]+)", "INDEX"),
                (r"\bCREATE\s+SCHEMA(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w\.]+)", "SCHEMA"),
                (r"\bCREATE\s+TYPE\s+([\w\.]+)", "TYPE"),
                (r"\bCREATE\s+SEQUENCE\s+([\w\.]+)", "SEQUENCE"),
                (r"\bCREATE\s+TRIGGER\s+([\w\.]+)", "TRIGGER"),
                (r"\bCREATE\s+POLICY\s+([\w\.]+)", "POLICY"),
                (r"\bCREATE\s+EXTENSION(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w\.]+)", "EXTENSION"),
            ]

            for pattern, object_type in patterns:
                match = re.search(pattern, raw_text, re.IGNORECASE)
                if match:
                    return clean_name(match.group(1)), object_type

            return "unknown_object", None

        if upper_text.startswith("ALTER"):

            patterns = [

                (r"\bALTER\s+TABLE\s+([\w\.]+)", "TABLE"),
                (r"\bALTER\s+VIEW\s+([\w\.]+)", "VIEW"),
                (r"\bALTER\s+INDEX\s+([\w\.]+)", "INDEX"),
                (r"\bALTER\s+FUNCTION\s+([\w\.]+)", "FUNCTION"),
                (r"\bALTER\s+SEQUENCE\s+([\w\.]+)", "SEQUENCE"),
                (r"\bALTER\s+TYPE\s+([\w\.]+)", "TYPE"),
                (r"\bALTER\s+SCHEMA\s+([\w\.]+)", "SCHEMA"),
            ]

            for pattern, object_type in patterns:
                match = re.search(pattern, raw_text, re.IGNORECASE)
                if match:
                    return clean_name(match.group(1)), object_type

            return "unknown_object", None

        if upper_text.startswith("DROP"):

            patterns = [

                (r"\bDROP\s+TABLE(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "TABLE"),
                (r"\bDROP\s+VIEW(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "VIEW"),
                (r"\bDROP\s+INDEX(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "INDEX"),
                (r"\bDROP\s+FUNCTION(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "FUNCTION"),
                (r"\bDROP\s+SEQUENCE(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "SEQUENCE"),
                (r"\bDROP\s+TYPE(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "TYPE"),
                (r"\bDROP\s+SCHEMA(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "SCHEMA"),
                (r"\bDROP\s+TRIGGER(?:\s+IF\s+EXISTS)?\s+([\w\.]+)", "TRIGGER"),
            ]

            for pattern, object_type in patterns:
                match = re.search(pattern, raw_text, re.IGNORECASE)
                if match:
                    return clean_name(match.group(1)), object_type

            return "unknown_object", None

        if upper_text.startswith("INSERT"):

            match = re.search(
                r"\bINSERT\s+INTO\s+([\w\.]+)",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            return "unknown_object", None

        if upper_text.startswith("UPDATE"):

            match = re.search(
                r"\bUPDATE\s+([\w\.]+)",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            return "unknown_object", None

        if upper_text.startswith("DELETE"):

            match = re.search(
                r"\bDELETE\s+FROM\s+([\w\.]+)",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            return "unknown_object", None

        if upper_text.startswith("SELECT"):

            match = re.search(
                r"\bFROM\s+([\w\.]+)",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            return "projection", None


        if upper_text.startswith("WITH"):

            # Return CTE name if present
            match = re.search(
                r"\bWITH\s+([\w\.]+)\s+AS\s*\(",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            # Otherwise return first FROM target
            match = re.search(
                r"\bFROM\s+([\w\.]+)",
                raw_text,
                re.IGNORECASE,
            )

            if match:
                return clean_name(match.group(1)), None

            return "projection", None

        return "unknown_object", None

    def _get_preceding_comments(self, node: Node) -> str:
        """Extracts preceding single-line (--) or multi-line (/* */) SQL comments above a statement."""
        parent = node.parent
        if not parent:
            return ""

        siblings = list(parent.children)
        try:
            idx = siblings.index(node)
        except ValueError:
            return ""

        comments = []
        for sibling in reversed(siblings[:idx]):
            if sibling.type in ("whitespace", "newline") and not self._node_text(sibling).strip():
                continue

            if sibling.type in ("comment", "marginalia"):
                raw_text = self._node_text(sibling).strip()
                if raw_text.startswith("--"):
                    comments.append(raw_text.lstrip("-").strip())
                elif raw_text.startswith("/*") and raw_text.endswith("*/"):
                    clean_lines = [
                        line.strip().lstrip("/*").rstrip("*/").strip("*").strip()
                        for line in raw_text.split("\n")
                        if line.strip()
                    ]
                    comments.append("\n".join(clean_lines))
                else:
                    break
            else:
                break

        comments.reverse()
        return "\n".join(comments)
    
    def _is_meaningful_sql(self, text: str) -> bool:

        stripped = text.strip()
        if not stripped:
            return False
        # only semicolons
        if stripped.replace(";", "").strip() == "":
            return False
        # remove single line comments
        stripped = re.sub(
            r"--.*?$",
            "",
            stripped,
            flags=re.MULTILINE
        )
        # remove multiline comments
        stripped = re.sub(
            r"/\*.*?\*/",
            "",
            stripped,
            flags=re.DOTALL
        )
        # remove semicolons again
        stripped = stripped.replace(";", "").strip()
        return bool(stripped)
    
    def _byte_to_line_column(self, byte_offset):

        text = self.file_content[:byte_offset].decode(
            "utf-8",
            errors="ignore"
        )
        lines = text.splitlines()
        if not lines:
            return 1,0
        return len(lines), len(lines[-1])

    def parse(self, tree) -> list:
        lang = Language(tssql.language())
        query = Query(lang, self.QUERY_SOURCE)
        cursor = QueryCursor(query)

        matches = list(cursor.matches(tree.root_node))

        ordered = []
        
        print("has_error:", tree.root_node.has_error)
        for child in tree.root_node.children:
                print(child.type)
                
        for pattern_idx, capture_dict in matches:
            defs = capture_dict.get("definition", [])
            if not defs:
                continue
            ordered.append((defs[0].start_byte, pattern_idx, capture_dict))

        ordered.sort(key=lambda x: x[0])

        self.entities = []
        processed_node_ids = set()
        consumed_bytes = set()

        for _, _, captures in ordered:
            node = captures["definition"][0]

            # Skip nested inner statements (e.g. subqueries inside a parent query byte range)
            if node.id in processed_node_ids or node.start_byte in consumed_bytes:
                continue

            raw_body = self._node_text(node).strip()
            if not raw_body:
                continue

            action_name = self._determine_statement_action(raw_body)
            table_target, target_type = self._extract_primary_entity_name(node)

            if action_name == "SELECT":
                name = f"SELECT FROM {table_target}"

            elif action_name == "WITH":
                name = f"WITH {table_target}"
                
            elif action_name == "INSERT":
                name = f"INSERT INTO {table_target}"
                
            elif action_name == "DELETE":
                name = f"DELETE FROM {table_target}"

            elif target_type:
                name = f"{action_name} {target_type} {table_target}"

            else:
                name = f"{action_name} {table_target}"

            parent_id = f"{self.file_path}#FILE"
            parent_type = "FILE"

            base_id = f"{self.file_path}#{name}"
            # Append line:column offset to prevent ID collision across identical query structures
            entity_id = f"{base_id}:{node.start_point[0] + 1}:{node.start_point[1]}"

            lines = [line.strip() for line in raw_body.split("\n") if line.strip()]
            signature = lines[0] if lines else name

            try:
                entity = {
                    "id": entity_id,
                    "name": name,
                    "entity_type": "SQL_STATEMENT",
                    "scope_range": {
                        "start_line": node.start_point[0] + 1,
                        "start_column": node.start_point[1],
                        "end_line": node.end_point[0] + 1,
                        "end_column": node.end_point[1],
                    },
                    "signature": signature,
                    "implementation_body": raw_body,
                    "associated_docstring": self._get_preceding_comments(node),
                    "parent_id": parent_id,
                    "parent_type": parent_type,
                }

                self.entities.append(entity)
                processed_node_ids.add(node.id)

                # Mark byte offsets inside this statement so nested sub-statements are ignored
                for b in range(node.start_byte, node.end_byte):
                    consumed_bytes.add(b)

            except Exception as exc:
                print(f"FAILED {name}: {exc}", flush=True)

        # Fallback for uncovered SQL regions

        if tree.root_node:
            uncovered_ranges = []
            start = None
            for i in range(len(self.file_content)):
                if i not in consumed_bytes:
                    if start is None:
                        start = i
                else:
                    if start is not None:
                        uncovered_ranges.append(
                            (start,i)
                        )
                        start = None

            if start is not None:
                uncovered_ranges.append(
                    (
                        start,
                        len(self.file_content)
                    )
                )

            for start,end in uncovered_ranges:
                raw_text = self.file_content[
                    start:end
                ].decode(
                    "utf-8",
                    errors="ignore"
                )

                if not self._is_meaningful_sql(raw_text):
                    continue

                start_line,start_column = (
                    self._byte_to_line_column(start)
                )

                end_line,end_column = (
                    self._byte_to_line_column(end)
                )


                self.entities.append(

                    {
                        "id":
                        f"{self.file_path}#SCRIPT:{start_line}:{start_column}",
                        "name": "unparsed_sql",
                        "entity_type": "SCRIPT",
                        "scope_range":{
                            "start_line":start_line,
                            "start_column":start_column,
                            "end_line":end_line,
                            "end_column":end_column
                        },
                        "signature": "-- Unparsed SQL",
                        "implementation_body": raw_text.strip(),
                        "associated_docstring": "",
                        "parent_id":
                        f"{self.file_path}#FILE",
                        "parent_type": "FILE"
                    }

                )

        # Maintain exact document order
        self.entities.sort(
            key=lambda e: (
                e["scope_range"]["start_line"],
                e["scope_range"]["start_column"],
            )
        )
        
        #for e in self.entities:
        #    print(e["entity_type"], e["name"])

        return self.entities  


import io
import json
import pandas as pd
from dateutil import parser
import numpy as np


class CSVHandler(BaseLanguageHandler):
    """
    CSV handler using pandas.

    Strategy
    --------
    1. Read the CSV using pandas.
    2. Infer schema & compute column-level statistics.
    3. Chunk rows into configurable sizes.
    4. Emit:
        - one CSV_SCHEMA entity
        - N CSV_ROWS entities
    """

    ROWS_PER_CHUNK = 1000

    def _is_int(self, value: str) -> bool:
        try:
            int(value)
            return True
        except Exception:
            return False

    def _is_float(self, value: str) -> bool:
        try:
            float(value)
            return True
        except Exception:
            return False

    def _is_bool(self, value: str) -> bool:
        return value.lower() in {
            "true",
            "false",
            "yes",
            "no",
            "0",
            "1",
        }

    def _infer_column_type(self, values: list[str]) -> str:
        """
        Infer a logical datatype for a column.

        Returns one of: INTEGER, FLOAT, BOOLEAN, DATETIME, STRING
        """
        filtered = [
            str(v).strip()
            for v in values
            if v is not None and pd.notna(v) and str(v).strip() != ""
        ]

        if not filtered:
            return "STRING"

        if all(self._is_int(v) for v in filtered):
            return "INTEGER"

        if all(self._is_float(v) for v in filtered):
            return "FLOAT"

        if all(self._is_bool(v) for v in filtered):
            return "BOOLEAN"

        datetime_success = 0
        for value in filtered:
            try:
                parser.parse(value)
                datetime_success += 1
            except Exception:
                pass

        if datetime_success >= len(filtered) * 0.90:
            return "DATETIME"

        return "STRING"

    def _build_column_statistics(self, df: pd.DataFrame) -> dict:
        """Produce metadata for every column in the dataframe."""
        metadata = {}

        for column in df.columns:
            series = df[column]

            # Collect raw values excluding NA for type inference
            non_na_values = series.dropna().tolist()
            col_type = self._infer_column_type(non_na_values)

            info = {
                "type": col_type,
                "null_count": int(series.isna().sum()),
                "unique_count": int(series.nunique(dropna=True)),
            }

            # Calculate min/max/avg ONLY if the inferred type is numeric
            if col_type in ("INTEGER", "FLOAT"):
                numeric = pd.to_numeric(series, errors="coerce").dropna()
                if not numeric.empty:
                    info["min"] = round(float(numeric.min()), 2)
                    info["max"] = round(float(numeric.max()), 2)
                    info["avg"] = round(float(numeric.mean()), 2)

            metadata[column] = info

        return metadata

    def _create_schema_entity(self, df: pd.DataFrame) -> dict:
        column_stats = self._build_column_statistics(df)

        schema = {
            "row_count": int(len(df)),
            "column_count": int(len(df.columns)),
            "columns": [],
        }

        for column in df.columns:
            stats = column_stats[column]
            col_entry = {
                "name": column,
                "type": stats["type"],
                "null_count": stats["null_count"],
                "unique_count": stats["unique_count"],
            }

            if "min" in stats:
                col_entry["min"] = stats["min"]
                col_entry["max"] = stats["max"]
                col_entry["avg"] = stats["avg"]

            schema["columns"].append(col_entry)

        implementation = json.dumps(
            schema,
            indent=2,
            ensure_ascii=False,
        )

        return {
            "id": f"{self.file_path}#SCHEMA",
            "name": "CSV_SCHEMA",
            "entity_type": "CSV_SCHEMA",
            "scope_range": {
                "start_line": 1,
                "start_column": 0,
                "end_line": 1,
                "end_column": 0,
            },
            "signature": "CSV_SCHEMA",
            "implementation_body": implementation,
            "associated_docstring": (
                f"Rows: {len(df)}\nColumns: {len(df.columns)}"
            ),
            "parent_id": f"{self.file_path}#FILE",
            "parent_type": "FILE",
        }

    def _create_row_chunk(self, df: pd.DataFrame, chunk_index: int, start_row: int, end_row: int,) -> dict:
        chunk = df.iloc[start_row:end_row]
        chunk = chunk.replace({np.nan: None})

        implementation = json.dumps(
            {
                "start_row": start_row + 1,
                "end_row": end_row,
                "row_count": len(chunk),
                "rows": chunk.to_dict(orient="records"),
            },
            ensure_ascii=False,
            separators=(",", ":")
        )

        return {
            "id": (f"{self.file_path}"f"#ROWS-{start_row + 1}-{end_row}"),
            "name": f"CSV_ROWS_{start_row + 1}_{end_row}",
            "entity_type": "CSV_ROWS",
            "scope_range": {
                "start_line": start_row + 2,  # +2 accounts for header and 1-based indexing
                "start_column": 0,
                "end_line": end_row + 1,
                "end_column": 0,
            },
            "signature": (f"CSV_ROWS "f"rows={start_row + 1}-{end_row} "f"count={len(chunk)}"),
            "implementation_body": implementation,
            "associated_docstring": (
                f"Rows: {len(chunk)}\nRange: {start_row + 1}-{end_row}"
            ),
            "parent_id": f"{self.file_path}#FILE",
            "parent_type": "FILE",
        }

    def parse(self, tree=None) -> list[dict]:
        self.entities = []

        # Handle bytes vs str input safely
        if isinstance(self.file_content, bytes):
            csv_text = self.file_content.decode("utf-8", errors="ignore")
        else:
            csv_text = self.file_content

        dataframe = pd.read_csv(
            io.StringIO(csv_text),
            keep_default_na=True,
        )

        # Emit Schema
        self.entities.append(self._create_schema_entity(dataframe))

        # Emit Data Chunks
        total_rows = len(dataframe)
        chunk_id = 1

        for start in range(0, total_rows, self.ROWS_PER_CHUNK):
            end = min(start + self.ROWS_PER_CHUNK, total_rows)
            entity = self._create_row_chunk(dataframe, chunk_id, start, end)
            self.entities.append(entity)
            chunk_id += 1

        return self.entities  

import os
from tree_sitter import Language, Parser

# Global registry placeholder
_LAZY_LANGUAGE_REGISTRY = None

_PARSER_CACHE = {}

def get_parser(grammar):
    key = id(grammar)
    if key not in _PARSER_CACHE:
        _PARSER_CACHE[key] = Parser(grammar)

    return _PARSER_CACHE[key]

def get_language_registry():
    """
    Lazily instantiates the tree-sitter grammars inside the active
    runtime namespace to prevent import-time binding failures.
    """
    global _LAZY_LANGUAGE_REGISTRY
    if _LAZY_LANGUAGE_REGISTRY is not None:
        return _LAZY_LANGUAGE_REGISTRY

    _LAZY_LANGUAGE_REGISTRY = {
        ".py": {"grammar": Language(tspython.language()), "handler": PythonHandler},
        ".go": {"grammar": Language(tsgo.language()), "handler": GoHandler},
        ".c": {"grammar": Language(tsc.language()), "handler": CHandler},
        ".h": {"grammar": Language(tsc.language()), "handler": CHandler},
        ".cpp": {"grammar": Language(tscpp.language()), "handler": CPPHandler},
        ".hpp": {"grammar": Language(tscpp.language()), "handler": CPPHandler},
        ".cc": {"grammar": Language(tscpp.language()), "handler": CPPHandler},
        ".java": {"grammar": Language(tsjava.language()), "handler": JavaHandler},
        ".rs": {"grammar": Language(tsrust.language()), "handler": RustHandler},
        ".ts": {"grammar": Language(tsts.language_typescript()), "handler": TSHandler},
        ".js": {"grammar": Language(tsjs.language()), "handler": JSHandler},
        ".jsx": {"grammar": Language(tsjs.language()), "handler": JSHandler},
        ".rb": {"grammar": Language(tsruby.language()), "handler": RubyHandler},
        ".css": {"grammar": Language(tscss.language()), "handler": CSSHandler},
        ".scss": {"grammar": Language(tscss.language()), "handler": CSSHandler},
        ".less": {"grammar": Language(tscss.language()), "handler": CSSHandler},
        ".html": {"grammar": Language(tshtmls.language()), "handler": HTMLHandler},
        ".json": {"grammar": Language(tsjson.language()), "handler": JSONHandler},
        ".md": {"grammar": Language(tsmarkdown.language()), "handler": MarkdownHandler},
        ".sql": {"grammar": Language(tssql.language()), "handler": SQLHandler}
    }
    return _LAZY_LANGUAGE_REGISTRY


def parse_codebase_file(file_path: str, file_content: str) -> list:
    """
    Unified entrypoint that branches cleanly between AST-dependent and flat file pathways.
    """
    print(
        f"FILE SIZE = {len(file_content)/1024/1024:.2f} MB",
        flush=True
    )
    ext = os.path.splitext(file_path)[1].lower()
    
    # 1. Bypass registry check entirely for csv files
    if ext == ".csv":
        csv_object = CSVHandler(file_path, file_content)
        return csv_object.parse()
        
    # 2. Grab dynamically initialized registry safely
    registry = get_language_registry()
    if ext not in registry:
        return []

    config = registry[ext]
    grammar = config.get("grammar")
    if not grammar:
        return []

    parser = get_parser(grammar)
    print("FILE READ OK", flush=True)
    tree = parser.parse(
        file_content.encode("utf-8")
    )
    #tree = parser.parse(bytes(file_content, "utf8"))
    print("TREE PARSED OK", flush=True)
    #tree = parser.parse(content.encode("utf-8"))

    handler = config["handler"](file_path, file_content)
    entities = handler.parse(tree)
    print(
        f"HANDLER RETURNED {len(entities)} ENTITIES",
        flush=True
    )
    return entities

# parse_codebase_file() is called from cloning_git_repo_locally_and_repo_file_parser.py script, passing the relative path and file content as arguments.

"""Compatible Lang & File Formats: Python, Go, C, Cpp, Java, Rust, TypeScript, JavaScript, Ruby, Css, Html, Json, Markdown, SQL, CSV Files"""