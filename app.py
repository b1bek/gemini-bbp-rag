import os
import time
import tempfile
import streamlit as st
import pandas as pd
import mimetypes
import logging

# Silence Streamlit/Tornado/Websocket request logs (aka webhook logs)
def _silence_network_logs():
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    logging.getLogger("tornado").setLevel(logging.CRITICAL)
    logging.getLogger("tornado.access").disabled = True
    logging.getLogger("tornado.application").disabled = True
    logging.getLogger("tornado.general").disabled = True
    logging.getLogger("websocket").disabled = True
    logging.getLogger("websockets").disabled = True
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)

_silence_network_logs()



# logging suppression initialized at top

# --- Helpers (top-level) ---
def guess_mime(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        ext = os.path.splitext(filename)[1].lower()
        common = {
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".json": "application/json",
            ".pdf": "application/pdf",
            ".html": "text/html",
            ".xml": "application/xml",
        }
        mime = common.get(ext)
    return mime or "application/octet-stream"

try:
    from google import genai
except Exception as e:
    genai = None

st.set_page_config(page_title="Gemini File Search Dashboard", layout="wide")
st.title("Gemini File Search Dashboard")

st.markdown(
    """
    This app lets you:
    - Create a File Search store (an index for your files)
    - Upload files and import them into the store
    - Ask questions with Gemini using File Search as RAG context
    """
)

if genai is None:
    st.error("google-genai is not installed. Please install dependencies and restart the app.")
    st.stop()

# --- Helpers ---
def get_client(api_key: str):
    """Initialize Gemini client using provided API key."""
    return genai.Client(api_key=api_key)


def ensure_store_in_state(store):
    st.session_state["file_search_store"] = store
    st.session_state["file_search_store_name"] = store.name

# NEW: General helpers to safely extract attributes and list stores/documents
def _safe_get(obj, attr, default=None):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def list_stores(client):
    """Return a list of File Search stores using the SDK pager if available."""
    stores = []
    try:
        pager = client.file_search_stores.list(config={"page_size": 20})
        # SDK may return a pager with .page/.has_next_page()/.next_page(), or a list-like
        if hasattr(pager, "page"):
            stores.extend(pager.page)
            try:
                while hasattr(pager, "has_next_page") and pager.has_next_page():
                    pager = pager.next_page()
                    stores.extend(getattr(pager, "page", []))
            except Exception:
                pass
        elif hasattr(pager, "stores"):
            stores = pager.stores
        elif isinstance(pager, (list, tuple)):
            stores = list(pager)
    except Exception as e:
        st.sidebar.error(f"Failed to list stores: {e}")
    return stores


def list_store_documents(client, store_name: str):
    """Return a list of Documents within the given File Search store."""
    docs = []
    try:
        pager = client.file_search_stores.documents.list(parent=store_name, config={"page_size": 20})
        if hasattr(pager, "page"):
            docs.extend(pager.page)
            try:
                while hasattr(pager, "has_next_page") and pager.has_next_page():
                    pager = pager.next_page()
                    docs.extend(getattr(pager, "page", []))
            except Exception:
                pass
        elif hasattr(pager, "documents"):
            docs = pager.documents
        elif isinstance(pager, (list, tuple)):
            docs = list(pager)
    except Exception as e:
        st.error(f"Failed to list store files: {e}")
    return docs


# --- Sidebar: API Key + Store selection ---
st.sidebar.header("Setup")
api_key_input = st.sidebar.text_input(
    "Google API Key",
    type="password",
    value="",
    help="Paste your Google API key."
)
effective_api_key = api_key_input.strip()
if not effective_api_key:
    st.sidebar.error("Missing API key. Enter your Google API key in the sidebar.")
    st.stop()
client = get_client(effective_api_key)

store_name_display = st.sidebar.text_input("File Search Store display name", value=st.session_state.get("file_search_store_display_name", "my-file-search-store"))

# Change to only create (no auto-select)
if st.sidebar.button("Create Store", use_container_width=True):
    try:
        store = client.file_search_stores.create(config={"display_name": store_name_display})
        st.success(f"Store created: {store.name}")
        # Refresh stores list so the newly created store appears
        st.session_state["stores_list"] = list_stores(client)
    except Exception as e:
        st.error(f"Failed to create store: {e}")

# NEW: Store listing & selection UI
st.sidebar.markdown("### Stores")
if st.sidebar.button("Refresh stores", use_container_width=True):
    st.session_state["stores_list"] = list_stores(client)

stores_list = st.session_state.get("stores_list", [])
if stores_list:
    # Organize: list all stores, click to activate (turns green)
    options_labels = [f"{_safe_get(s, 'display_name', '(no display name)')} | {_safe_get(s, 'name', '')}" for s in stores_list]
    label_to_store = {options_labels[i]: stores_list[i] for i in range(len(options_labels))}
    active_name = st.session_state.get("file_search_store_name", "")
    default_idx = 0
    for i, s in enumerate(stores_list):
        if _safe_get(s, "name", "") == active_name:
            default_idx = i
            break
    selected_label = st.sidebar.radio("Stores", options=options_labels, index=default_idx, key="stores_radio")
    selected_store = label_to_store.get(selected_label)

    # Activate immediately and show green indicator
    if selected_store:
        sel_name = _safe_get(selected_store, "name", "")
        if sel_name and sel_name != active_name:
            ensure_store_in_state(selected_store)
            st.session_state["file_search_store_display_name"] = _safe_get(selected_store, "display_name", "")
            st.session_state["store_documents"] = list_store_documents(client, sel_name)
            st.session_state["_store_docs_for"] = sel_name
        st.sidebar.success(f"Active store: {sel_name}")

    # Delete active store (the currently selected one)
    delete_clicked = st.sidebar.button("Delete active store", use_container_width=True, disabled=(selected_store is None))
    if delete_clicked and selected_store:
        store_to_delete_name = _safe_get(selected_store, "name", "")
        if not store_to_delete_name:
            st.sidebar.error("No store resource name available for deletion.")
        else:
            try:
                client.file_search_stores.delete(name=store_to_delete_name)
                st.sidebar.success(f"Deleted store: {store_to_delete_name}")
                # Clear selection if this was the active store
                if st.session_state.get("file_search_store_name") == store_to_delete_name:
                    for k in ["file_search_store", "file_search_store_name", "store_documents", "_store_docs_for"]:
                        st.session_state.pop(k, None)
            except Exception as e1:
                st.sidebar.error(f"Failed to delete store: {e1}")
            # Refresh stores list
            st.session_state["stores_list"] = list_stores(client)
else:
    st.sidebar.info("Click 'Refresh stores' to load available File Search stores.")

store_name = st.session_state.get("file_search_store_name")

# --- Main: Upload & Index, Ask ---
tabs = st.tabs(["Upload & Index", "Ask Questions"])

with tabs[0]:
    st.subheader("Upload a file to index")
    if not store_name:
        st.info("Create or select a File Search store in the sidebar first.")
    else:
        # Track uploads across the session



        st.markdown(f"Current store: `{store_name}`")

        # NEW: Auto-load docs for active store on first render or when store changes
        if "store_documents" not in st.session_state or st.session_state.get("_store_docs_for") != store_name:
            st.session_state["store_documents"] = list_store_documents(client, store_name)
            st.session_state["_store_docs_for"] = store_name

        # NEW: List files within the selected store
        with st.expander("Store files in this File Search store", expanded=True):
            if st.button("Refresh store files"):
                st.session_state["store_documents"] = list_store_documents(client, store_name)
            docs = st.session_state.get("store_documents", [])
            if docs:
                rows = []
                for d in docs:
                    rows.append({
                        "display_name": _safe_get(d, "display_name", ""),
                        "name": _safe_get(d, "name", ""),
                        "create_time": _safe_get(d, "create_time", ""),
                        "update_time": _safe_get(d, "update_time", ""),
                    })
                try:
                    st.table(pd.DataFrame(rows))
                except Exception:
                    # Fallback to JSON view if DataFrame construction fails
                    st.json([getattr(d, "to_dict", lambda: str(d))() for d in docs])

                # NEW: Document deletion controls
                options_keys = [
                    _safe_get(d, "name", "") for d in docs if _safe_get(d, "name", "")
                ]
                label_map = {
                    _safe_get(d, "name", ""): f"{_safe_get(d, 'display_name', '(no display name)')} | {_safe_get(d, 'name', '')}"
                    for d in docs
                }
                # Simplified deletion UI: either select all OR choose specific documents, then one delete action
                select_all_docs = st.checkbox("Select all documents", key="_select_all_docs")
                if select_all_docs:
                    st.info(f"All {len(options_keys)} documents will be deleted.")
                    selected_doc_names = options_keys
                else:
                    selected_doc_names = st.multiselect(
                        "Select documents to delete",
                        options=options_keys,
                        format_func=lambda x: label_map.get(x, x),
                        key="_selected_doc_names_some",
                    )
                delete_docs_clicked = st.button(
                    "Delete",
                    use_container_width=True,
                    disabled=(not selected_doc_names)
                )
                if delete_docs_clicked and selected_doc_names:
                    results = []
                    for doc_name in selected_doc_names:
                        try:
                            client.file_search_stores.documents.delete(name=doc_name, config={"force": True})
                            results.append({"document": doc_name, "status": "deleted"})
                        except Exception as e1:
                            results.append({"document": doc_name, "status": f"error: {e1}"})
                    # Refresh documents list after deletion
                    st.session_state["store_documents"] = list_store_documents(client, store_name)
                    st.success("Deletion attempted. See results below.")
                    with st.expander("Delete results"):
                        try:
                            st.table(pd.DataFrame(results))
                        except Exception:
                            st.json(results)
            else:
                st.write("No files listed yet. Click 'Refresh store files' to load documents.")

        # Uploaded files (this session) list removed per request

        uploaded_files = st.file_uploader(
            "Choose files to upload and index",
            type=None,
            accept_multiple_files=True
        )

        upload_clicked = st.button(
            "Upload & Import Selected Files",
            disabled=not uploaded_files
        )
        if upload_clicked:
            if not uploaded_files:
                st.warning("Please select at least one file.")
            else:
                results = []
                for f in uploaded_files:
                    try:
                        # Save to a temporary file because SDK expects a file path
                        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(f.name)[1]) as tmp:
                            tmp.write(f.getbuffer())
                            temp_path = tmp.name

                        base_name = os.path.splitext(f.name)[0]
                        display_name = base_name
                        mime_type = guess_mime(f.name)
                        st.write(f"Uploading to File Search store: {f.name} (detected mime: {mime_type})")
                        st.write(f"Uploading & importing into File Search store: {store_name} (display name: {display_name})")
                        operation = client.file_search_stores.upload_to_file_search_store(
                            file=temp_path,
                            file_search_store_name=store_name,
                            config={"display_name": display_name},
                        )

                        # Poll until import is complete
                        with st.spinner(f"Indexing {f.name} (polling operation status)..."):
                            while not getattr(operation, "done", False):
                                time.sleep(2)
                                operation = client.operations.get(operation)

                        results.append({"file": f.name, "display_name": display_name, "mime_type": mime_type, "status": "success"})

                        st.success(f"Indexed: {f.name}")
                    except Exception as e:
                        results.append({"file": f.name, "display_name": display_name, "status": f"error: {e}"})

                        st.error(f"Upload/import failed for {f.name}: {e}")
                    finally:
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass


                with st.expander("Batch results"):
                    st.table(results)

                # Uploaded files (this session) list removed per request

with tabs[1]:
    st.subheader("Ask Gemini with File Search context")
    if not store_name:
        st.info("Create or select a File Search store in the sidebar first.")
    else:
        question = st.text_area("Your question", placeholder="Ask something about your uploaded files...")
        model_name = st.selectbox("Model", ["gemini-2.5-flash", "gemini-2.5-pro"], index=0)
        use_default_prompt = st.checkbox("Use default system prompt (Bug bounty program search)", value=True, help="When unchecked, the hardcoded system instruction is excluded.", key="_use_default_sys_prompt")
        ask = st.button("Ask")

        if ask and question.strip():
            try:
                # Build the tool configuration referencing the File Search store and enforce JSON-only output
                # Add system prompt per your requirement
                sys_prompt = None
                if use_default_prompt:
                    sys_prompt = (
                        """"You are a specialized tool for verifying the existence and details of a bug bounty program based on the user's input. "
                            "**Strictly use the File Search tool on the provided vector store** to find a matching bug bounty program. "
                            "If a match is found, extract the required details. "
                            "**Respond strictly in a single JSON object only**, with no explanations, extra text, or markdown formatting (e.g., no ```json ```). "
                            "The required fields are: "
                            "* **'Found'**: (string, 'Yes' or 'No') — Indicate if a bug bounty program was found for the input. "
                            "* **'Source'**: (string, the name or ID of the document/file in the vector store where the information was found, or 'N/A' if not found). "
                            "* **'Rewards'**: (string, 'Yes' or 'No') — Indicate if the program offers monetary or non-monetary rewards. "
                            "Example of expected output: {'Found': 'Yes', 'Source': 'vector_store_doc_123', 'Rewards': 'Yes'}" """
                        f"Input: {question.strip()}"
                    )

                config = {
                    "tools": [
                        {"file_search": {"file_search_store_names": [store_name]}}
                    ],
                }
                if use_default_prompt:
                    config["system_instruction"] = sys_prompt

                response = client.models.generate_content(
                    model=model_name,
                    contents=question.strip(),
                    config=config,
                )

                # Display the response simply
                st.markdown("### Answer")
                st.write(getattr(response, "text", "") or "")

                # Tool call diagnostics removed

                # Optionally show raw response for debugging
                with st.expander("Raw response"):
                    if hasattr(response, "to_dict"):
                        st.json(response.to_dict())
                    else:
                        st.code(str(response))

            except Exception as e:
                st.error(f"Generation failed: {e}")
                with st.expander("Error details"):
                    st.code(repr(e))
        elif ask:
            st.warning("Please enter a question.")

# logging suppression initialized at top