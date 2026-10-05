import os
import json
import tkinter as tk
from tkinter import ttk, messagebox
from ttkthemes import ThemedTk

INDEX_FILE = r"c:\Users\James\Documents\coding\wildlife projecxts\animal_index.json"

class ModernWildlifeBrowser:
    def __init__(self, root):
        self.root = root
        self.root.title("Wildlife Catalog Master Index Engine")
        self.root.geometry("1100x700")
        
        # Load your actual catalog JSON database
        self.load_database()
        
        # Control & Binding variables
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.update_view)
        self.review_only_var = tk.BooleanVar(value=False)
        
        # Build layout architecture components
        self.create_styles()
        self.create_layout()
        self.update_view()

    def load_database(self):
        if os.path.exists(INDEX_FILE):
            with open(INDEX_FILE, 'r') as f:
                self.db = json.load(f)
        else:
            self.db = {}

    def create_styles(self):
        """Injects custom style metrics to completely modernize old Tkinter elements."""
        self.style = ttk.Style(self.root)
        
        # FIXED: Removed all decimals from font sizes to prevent TclError crash
        self.style.configure(".", font=("Segoe UI", 11))
        self.style.configure("Treeview.Heading", font=("Segoe UI Semibold", 11), padding=6)
        self.style.configure("Treeview", font=("Segoe UI", 10), rowheight=28)
        
        # Add color states to distinct heading rows
        self.style.configure("Header.TLabel", font=("Segoe UI Semibold", 14))
        self.style.configure("Metric.TLabel", font=("Segoe UI", 11, "bold"), foreground="#007acc")

    def create_layout(self):
        # Top Control & Query Bar Section
        top_bar = ttk.LabelFrame(self.root, text=" Filter & Catalog Controls ", padding=15)
        top_bar.pack(fill=tk.X, padx=15, pady=10)
        
        # FIXED: Changed font size tuple to use integer 11
        ttk.Label(top_bar, text="Search Species / File:", font=("Segoe UI Semibold", 11)).pack(side=tk.LEFT, padx=5)
        search_entry = ttk.Entry(top_bar, textvariable=self.search_var, width=35)
        search_entry.pack(side=tk.LEFT, padx=5)
        search_entry.focus() # Instant typing readiness focus hook
        
        ttk.Checkbutton(
            top_bar, 
            text="Isolate [⚠️ REVIEW NEEDED] Frames", 
            variable=self.review_only_var,
            command=self.update_view
        ).pack(side=tk.LEFT, padx=25)
        
        # Metrics Display Panel Banner
        self.metrics_lbl = ttk.Label(self.root, text="", style="Header.TLabel", padding=5)
        self.metrics_lbl.pack(anchor="w", padx=20)
        
        # Main Data Matrix Table Grid Container Frame
        table_container = ttk.Frame(self.root, padding=15)
        table_container.pack(fill=tk.BOTH, expand=True)
        
        columns = ("filename", "label", "scene", "status")
        self.tree = ttk.Treeview(table_container, columns=columns, show="headings", selectmode="browse")
        
        self.tree.heading("filename", text="📸  Raw Camera Filename")
        self.tree.heading("label", text="🏷️  Classified Animal Species")
        self.tree.heading("scene", text="📐  Scene Composition")
        self.tree.heading("status", text="🛡️  Index Validation Status")
        
        self.tree.column("filename", width=220, anchor="w")
        self.tree.column("label", width=280, anchor="w")
        self.tree.column("scene", width=160, anchor="center")
        self.tree.column("status", width=160, anchor="center")
        
        scroll_y = ttk.Scrollbar(table_container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bind double-click event tracking map to trigger native viewer launchers
        self.tree.bind("<Double-1>", self.launch_native_viewer)
        self.tree.bind("<<TreeviewSelect>>", self.populate_modifier_fields)
        
        # Bottom Label Refinement Control Box Panel
        edit_panel = ttk.LabelFrame(self.root, text=" Modify Selected Metadata Entry Row Data ", padding=15)
        edit_panel.pack(fill=tk.X, padx=15, pady=15)
        
        ttk.Label(edit_panel, text="Corrected Name Label:").pack(side=tk.LEFT, padx=5)
        self.edit_entry = ttk.Entry(edit_panel, width=40)
        self.edit_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(edit_panel, text="Save Index Changes", command=self.commit_entry_correction).pack(side=tk.LEFT, padx=15)

    def update_view(self, *args):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        search_query = self.search_var.get().lower().strip()
        review_only = self.review_only_var.get()
        
        match_count = 0
        review_count = 0
        
        for key, data in self.db.items():
            label = data.get("animal_label", "Unknown")
            scene = data.get("scene_type", "single_subject").replace("_", " ").title()
            needs_check = data.get("requires_manual_check", False)
            
            if needs_check:
                review_count += 1
                
            if search_query and search_query not in label.lower() and search_query not in key.lower():
                continue
            if review_only and not needs_check:
                continue
                
            status_text = "⚠️ Review Needed" if needs_check else "✅ Verified OK"
            self.tree.insert("", tk.END, iid=key, values=(key, label, scene, status_text))
            match_count += 1
            
        self.metrics_lbl.config(
            text=f"Showing {match_count} of {len(self.db)} Mapped Records  |  ({review_count} Flagged For Manual Check)"
        )

    def populate_modifier_fields(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        # FIXED: Corrected multi-value assignment unpacking from index array
        item_data = self.tree.item(selected)['values']
        if item_data and len(item_data) > 1:
            current_label = str(item_data[1])
            self.edit_entry.delete(0, tk.END)
            if "common name" not in current_label.lower():
                self.edit_entry.insert(0, current_label)

    def launch_native_viewer(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        # FIXED: Selection returns tuple list string keys
        file_id = selected[0] if isinstance(selected, (list, tuple)) else selected
        full_path = self.db[file_id]["file_path"]
        try:
            os.startfile(full_path)
        except Exception as e:
            messagebox.showerror("IO Execution Error", f"Could not launch native camera file mapping asset:\n{e}")

    def commit_entry_correction(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Selection Input Missing", "Highlight a dataset file row in the list grid view table framework first!")
            return
            
        file_id = selected[0] if isinstance(selected, (list, tuple)) else selected
        cleaned_text = self.edit_entry.get().strip()
        
        if not cleaned_text:
            return
            
        self.db[file_id]["animal_label"] = cleaned_text
        self.db[file_id]["requires_manual_check"] = False  # Clear review flag upon manual verification override
        
        with open(INDEX_FILE, "w") as f:
            json.dump(self.db, f, indent=4)
            
        self.edit_entry.delete(0, tk.END)
        self.update_view()

if __name__ == "__main__":
    # Bootstrapping a clean, themeable Window framework engine context layer
    root = ThemedTk(theme="arc") # Uses 'arc' for a clean, light flat professional layout look
    app = ModernWildlifeBrowser(root)
    root.mainloop()
