# SE頭無音カット
# ステップ3：画面を付ける
#   「フォルダを選ぶ」「ファイルを選ぶ」で一覧に入れ、「頭の無音をカット」でまとめて処理する
#   切る処理そのものは trim_head.py の係にまかせる（画面と処理の仕事を分ける）
#   無音の基準（dB）と余白（ミリ秒）は、画面で変えられる
# ステップ4：おかしな数字の入力、ffmpeg がないとき、思わぬ失敗でも、画面が止まらないようにする
# 追加    ：画面にファイルやフォルダをドラッグ＆ドロップしても、一覧に足せるようにする
# 追加    ：デザインを、動画編集ソフトになじむダーク（暗い色）に作り直す
#           点線の「ドロップ場所」を作り、ファイルを重ねると枠が緑に光るようにする
#
# 動かし方：python app.py
# 必要なもの：ffmpeg、tkinterdnd2（ドラッグ＆ドロップ用。入れ方：python -m pip install tkinterdnd2）

import ctypes
import math
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk  # ttk：一覧の表（Treeview）など、tkinter の追加の部品

from tkinterdnd2 import DND_FILES, TkinterDnD  # ドラッグ＆ドロップを受け取るための追加の部品

from trim_head import (AUDIO_EXTENSIONS, CUT, DEFAULT_MARGIN_MS, DEFAULT_THRESHOLD_DB, ERROR, OUTPUT_FOLDER, SKIP,
                       FfmpegMissing, collect_files, summary, trim_many)

MIN_DB = -120        # 無音の大きさに入れられる、いちばん小さい数字（これより小さい音は、ふつう記録されない）
MAX_MARGIN_MS = 1000  # 余白に入れられる、いちばん大きい数字（1秒）

# ファイルを選ぶ画面で使う、拡張子の書き方（例：「*.wav *.mp3」）
AUDIO_PATTERNS = " ".join("*" + ext for ext in sorted(AUDIO_EXTENSIONS))
# 画面に出す、使える形式の名前（例：「AIF・AIFF・FLAC…」）
AUDIO_NAMES = "・".join(ext[1:].upper() for ext in sorted(AUDIO_EXTENSIONS))

# ---- 色（ダーク）----
BG = "#1c1d20"           # いちばん後ろの背景
PANEL = "#26282c"        # 一覧や設定の下じき
PANEL_HOVER = "#30333a"  # ふつうのボタンにマウスを乗せたとき
HEADING = "#2e3136"      # 一覧の見出し
BORDER = "#3a3e45"       # 枠線
TEXT = "#ececec"         # ふつうの文字
SUB = "#9aa0a8"          # 説明など、ひかえめな文字
DIM = "#5d636b"          # 押せないボタンの文字
ACCENT = "#1fa58f"       # 差し色（サムネイルと同じ緑）
ACCENT_HOVER = "#27bba3"  # 差し色のボタンにマウスを乗せたとき
SELECTED = "#24453f"     # 一覧で選んだ行

# 結果ごとの文字の色（暗い背景でも読みやすい明るさにする）
STATUS_COLORS = {CUT: "#5fd4a4", SKIP: "#8d939b", ERROR: "#ff7b72"}

FONT = "Yu Gothic UI"  # Windows に入っている、読みやすい日本語の書体


class FlatButton(tk.Button):
    """平らなボタン。マウスを乗せると色が変わり、押せないときは暗くなる
    kind="accent" は緑の目立つボタン、kind="normal" はひかえめなボタン"""

    def __init__(self, parent, text, command, kind="normal", **options):
        self.colors = {
            "accent": (ACCENT, ACCENT_HOVER, "#ffffff"),
            "normal": (PANEL, PANEL_HOVER, TEXT),
        }[kind]
        bg, _, fg = self.colors
        super().__init__(parent, text=text, command=command, bg=bg, fg=fg, activebackground=self.colors[1],
                         activeforeground=fg, disabledforeground=DIM, relief="flat", bd=0, highlightthickness=0,
                         cursor="hand2", **options)
        self.bind("<Enter>", lambda e: self["state"] == "normal" and self.config(bg=self.colors[1]))
        self.bind("<Leave>", lambda e: self.config(bg=self.colors[0] if self["state"] == "normal" else PANEL))

    def set_enabled(self, enabled):
        self.config(state="normal" if enabled else "disabled",
                    bg=self.colors[0] if enabled else PANEL, cursor="hand2" if enabled else "arrow")


def use_dark_title_bar(root):
    """Windows の窓の上の帯（タイトルバー）も暗くする（Windows 10 の新しい版・11 で効く。だめでも気にしない）"""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # 20：暗い帯にする指定
    except Exception:
        pass


def setup_styles(root):
    """ttk の部品（一覧・スクロールバー・進み具合の棒）を暗い色にする
    tkinter に最初から入っている「clam」という見た目を土台にすると、色を自由に変えられる"""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, rowheight=30, font=(FONT, 10))
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=HEADING, foreground=SUB, relief="flat", borderwidth=0,
                    font=(FONT, 9, "bold"), padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", HEADING)])
    style.configure("Vertical.TScrollbar", background=HEADING, troughcolor=PANEL, bordercolor=PANEL,
                    lightcolor=HEADING, darkcolor=HEADING, arrowcolor=SUB, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", PANEL_HOVER)])
    style.configure("Accent.Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT, bordercolor=PANEL,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)


class App:
    def __init__(self, root):
        self.root = root
        self.files = []  # 一覧に入っているファイル（Path）
        self.busy = False  # 処理中かどうか（処理中は、ドラッグ＆ドロップを受け付けない）
        # 処理は別の流れ（スレッド）で動かすので、進み具合を画面に伝えるための受け渡し場所
        self.messages = queue.Queue()

        root.title("SE頭無音カット")
        root.configure(bg=BG)
        root.minsize(760, 660)
        root.option_add("*Font", (FONT, 10))
        setup_styles(root)
        use_dark_title_bar(root)

        frame = tk.Frame(root, bg=BG, padx=24, pady=20)
        frame.pack(fill="both", expand=True)

        # 題名と説明
        tk.Label(frame, text="SE頭無音カット", bg=BG, fg=TEXT, font=(FONT, 18, "bold"), anchor="w").pack(fill="x")
        tk.Label(frame, text=f"SE の頭の無音だけを切ります。最後の無音と元のファイルは残し、"
                             f"切ったものは同じ場所の「{OUTPUT_FOLDER}」フォルダに保存します",
                 bg=BG, fg=SUB, anchor="w", justify="left").pack(fill="x", pady=(2, 16))

        # ドロップ場所（点線の枠。ファイルを重ねると枠が緑になる）
        self.drop_zone = tk.Canvas(frame, bg=BG, height=128, highlightthickness=0)
        self.drop_zone.pack(fill="x")
        inner = tk.Frame(self.drop_zone, bg=BG)
        tk.Label(inner, text="ここにファイルやフォルダをドロップ", bg=BG, fg=TEXT, font=(FONT, 12, "bold")).pack()
        tk.Label(inner, text=f"対応：{AUDIO_NAMES}", bg=BG, fg=SUB, font=(FONT, 9)).pack(pady=(2, 10))
        buttons = tk.Frame(inner, bg=BG)
        buttons.pack()
        self.folder_button = FlatButton(buttons, "フォルダを選ぶ", self.choose_folder, padx=18, pady=6)
        self.folder_button.pack(side="left")
        self.file_button = FlatButton(buttons, "ファイルを選ぶ", self.choose_files, padx=18, pady=6)
        self.file_button.pack(side="left", padx=(10, 0))
        self.drop_inner = self.drop_zone.create_window(0, 0, window=inner)
        self.drop_color = BORDER
        self.drop_zone.bind("<Configure>", lambda e: self.draw_drop_zone())

        # 一覧の見出しと「一覧を空にする」
        list_head = tk.Frame(frame, bg=BG)
        list_head.pack(fill="x", pady=(18, 6))
        tk.Label(list_head, text="ファイル一覧", bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
        self.clear_button = FlatButton(list_head, "一覧を空にする", self.clear, padx=12, pady=3)
        self.clear_button.pack(side="right")

        # 一覧（ファイル名・結果・理由の3列の表）
        table_frame = tk.Frame(frame, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=("name", "status", "message"), show="headings", height=8)
        self.table.heading("name", text="ファイル名", anchor="w")
        self.table.heading("status", text="結果")
        self.table.heading("message", text="くわしく", anchor="w")
        self.table.column("name", width=240)
        self.table.column("status", width=80, anchor="center")
        self.table.column("message", width=380)
        for status, color in STATUS_COLORS.items():
            self.table.tag_configure(status, foreground=color)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # 設定（無音の基準・余白）
        settings = tk.Frame(frame, bg=PANEL, padx=16, pady=12, highlightthickness=1, highlightbackground=BORDER)
        settings.pack(fill="x", pady=(14, 0))
        self.db_var = tk.StringVar(value=str(DEFAULT_THRESHOLD_DB))
        self.margin_var = tk.StringVar(value=str(DEFAULT_MARGIN_MS))
        for label, var, unit in (("無音とみなす大きさ", self.db_var, "dB 以下"),
                                 ("音の直前に残す余白", self.margin_var, "ミリ秒")):
            tk.Label(settings, text=label, bg=PANEL, fg=SUB).pack(side="left")
            tk.Entry(settings, textvariable=var, width=6, justify="right", bg=BG, fg=TEXT, insertbackground=TEXT,
                     relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                     font=(FONT, 11)).pack(side="left", padx=(8, 0), ipady=3)
            tk.Label(settings, text=unit, bg=PANEL, fg=SUB).pack(side="left", padx=(6, 28))

        # 実行ボタンと、進み具合の棒
        run_row = tk.Frame(frame, bg=BG)
        run_row.pack(fill="x", pady=(16, 0))
        self.run_button = FlatButton(run_row, "頭の無音をカット", self.start, kind="accent",
                                     font=(FONT, 12, "bold"), padx=28, pady=10)
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(run_row, mode="determinate", style="Accent.Horizontal.TProgressbar")
        self.progress.pack(side="left", fill="x", expand=True, padx=(16, 0))

        # 状態の表示（何個選んだか、進み具合、まとめ、まちがいの知らせ）
        self.status_label = tk.Label(frame, text="", bg=BG, fg=SUB, anchor="w", justify="left")
        self.status_label.pack(fill="x", pady=(10, 0))

        self.refresh()
        self.register_drop(root)

    # ---- ドロップ場所の見た目 ----
    def draw_drop_zone(self):
        """点線の枠を、いまの大きさと色で描き直し、中身をまん中に置く"""
        c = self.drop_zone
        w, h = c.winfo_width(), c.winfo_height()
        c.delete("border")
        c.create_rectangle(1, 1, w - 2, h - 2, outline=self.drop_color, dash=(6, 4), width=2, tags="border")
        c.coords(self.drop_inner, w / 2, h / 2)

    def highlight_drop_zone(self, on):
        self.drop_color = ACCENT if on and not self.busy else BORDER
        self.draw_drop_zone()

    # ---- ドラッグ＆ドロップ ----
    def register_drop(self, widget):
        """widget とその中の部品すべてで、ドラッグ＆ドロップを受け取れるようにする"""
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self.on_drop)
        widget.dnd_bind("<<DropEnter>>", self.on_drag_enter)
        widget.dnd_bind("<<DropLeave>>", self.on_drag_leave)
        for child in widget.winfo_children():
            self.register_drop(child)

    def on_drag_enter(self, event):
        self.highlight_drop_zone(True)
        return event.action

    def on_drag_leave(self, event):
        self.highlight_drop_zone(False)
        return event.action

    def on_drop(self, event):
        """ファイルやフォルダが落とされたとき：フォルダと音声ファイルだけを一覧に足す"""
        self.highlight_drop_zone(False)
        if self.busy:
            return  # 処理中は、一覧が途中で変わらないように受け付けない
        # 落とされたものの一覧は1つの文字列で届く。空白を含む名前は { } で囲まれているので、splitlist で分ける
        paths = [Path(p) for p in self.root.tk.splitlist(event.data)]
        accepted = [p for p in paths if p.is_dir() or p.suffix.lower() in AUDIO_EXTENSIONS]
        rejected = len(paths) - len(accepted)
        before = len(self.files)
        self.add_files(accepted)
        message = f"{len(self.files) - before} 個のファイルを入れました（全部で {len(self.files)} 個）"
        if rejected:
            message += f"。音声ファイルではないものが {rejected} 個あったので、入れませんでした"
        self.status_label.config(text=message)

    # ---- ファイルを選ぶ ----
    def choose_folder(self):
        folder = filedialog.askdirectory(title="SE が入ったフォルダを選ぶ")
        if folder:
            self.add_files([folder])

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="SE のファイルを選ぶ（複数選べます）",
            filetypes=[("音声ファイル", AUDIO_PATTERNS), ("すべてのファイル", "*.*")])
        if paths:
            self.add_files(paths)

    def add_files(self, paths):
        """選んだファイル・フォルダを一覧に足す（すでに一覧にあるものは、もう一度入れない）"""
        self.files = collect_files([*self.files, *paths])
        self.refresh()

    def clear(self):
        self.files = []
        self.refresh()

    def refresh(self, message=None):
        """一覧の表を、いまの self.files に合わせて書き直す（結果の列は空に戻す）"""
        self.table.delete(*self.table.get_children())
        for f in self.files:
            # 処理する前は、どのフォルダのファイルかだけを出す（長い場所をそのまま出すと読みにくいため）
            self.table.insert("", "end", values=(f.name, "", f"フォルダ：{f.parent.name}"))
        self.progress["value"] = 0
        self.status_label.config(text=message or f"{len(self.files)} 個のファイルが選ばれています", fg=SUB)
        # ファイルを選ぶまでは、実行ボタンを押せないようにする
        self.run_button.set_enabled(bool(self.files))

    # ---- 処理する ----
    def read_settings(self):
        """入力欄の数字を読む。おかしな数字のときは、理由を画面に出して None を返す"""
        def warn(text):
            self.status_label.config(text=text, fg=STATUS_COLORS[ERROR])

        try:
            db = float(self.db_var.get())
            margin = float(self.margin_var.get())
        except ValueError:
            return warn("無音の大きさと余白には、数字を入れてください")
        # 「nan」や「inf」も数字として読めてしまうので、ふつうの数字かどうかも確かめる
        if not (math.isfinite(db) and math.isfinite(margin)):
            return warn("無音の大きさと余白には、ふつうの数字を入れてください")
        if not MIN_DB <= db < 0:
            return warn(f"無音の大きさは、{MIN_DB} 〜 0 より小さい数字（マイナス）で入れてください（例：-50）")
        if not 0 <= margin <= MAX_MARGIN_MS:
            return warn(f"余白は 0 〜 {MAX_MARGIN_MS} ミリ秒で入れてください")
        return db, margin

    def start(self):
        settings = self.read_settings()
        if settings is None:
            return
        self.refresh(message="処理しています…")
        self.set_busy(True)
        self.progress["maximum"] = len(self.files)
        # 処理は別の流れで動かす（たくさんあっても、画面が「応答なし」にならないように）
        threading.Thread(target=self.work, args=(list(self.files), *settings), daemon=True).start()
        self.root.after(100, self.check_messages)

    def work(self, files, db, margin):
        """別の流れで動く部分。画面の部品には触れず、受け渡し場所に結果を入れるだけ
        どんな失敗でも必ず「終わった」ことを伝え、画面が「処理しています…」のまま止まらないようにする"""
        try:
            results = trim_many(files, db, margin,
                                on_progress=lambda i, n, r: self.messages.put(("progress", i, n, r)))
            self.messages.put(("done", results))
        except FfmpegMissing as e:
            self.messages.put(("failed", str(e)))
        except Exception as e:  # 考えていなかった失敗
            self.messages.put(("failed", f"思わぬエラーで止まりました（{e}）"))

    def check_messages(self):
        """画面の流れから、受け渡し場所を見に行って、届いた結果を画面に出す"""
        while not self.messages.empty():
            message = self.messages.get()
            if message[0] == "progress":
                _, i, n, result = message
                row = self.table.get_children()[i - 1]
                self.table.item(row, values=(result["file"].name, result["status"], result["message"]),
                                tags=(result["status"],))
                self.table.see(row)
                self.progress["value"] = i
                self.status_label.config(text=f"処理しています… {i} / {n}")
            elif message[0] == "done":
                _, results = message
                self.set_busy(False)
                self.status_label.config(text=f"終わりました：{summary(results)}", fg=TEXT)
                return  # 終わったので、もう見に行かない
            else:  # "failed"：途中で止まった
                _, text = message
                self.set_busy(False)
                self.status_label.config(text=text, fg=STATUS_COLORS[ERROR])
                return
        self.root.after(100, self.check_messages)

    def set_busy(self, busy):
        """処理中は、ボタンを押せないようにする（途中で一覧が変わらないように）"""
        self.busy = busy
        for button in (self.folder_button, self.file_button, self.clear_button, self.run_button):
            button.set_enabled(not busy)


if __name__ == "__main__":
    root = TkinterDnD.Tk()  # ドラッグ＆ドロップを受け取れる画面
    App(root)
    root.mainloop()
