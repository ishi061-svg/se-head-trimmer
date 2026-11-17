# SE頭無音カット
# ステップ3：画面を付ける
#   「フォルダを選ぶ」「ファイルを選ぶ」で一覧に入れ、「頭の無音をカット」でまとめて処理する
#   切る処理そのものは trim_head.py の係にまかせる（画面と処理の仕事を分ける）
#   無音の基準（dB）と余白（ミリ秒）は、画面で変えられる
# ステップ4：おかしな数字の入力、ffmpeg がないとき、思わぬ失敗でも、画面が止まらないようにする
# 追加    ：画面にファイルやフォルダをドラッグ＆ドロップしても、一覧に足せるようにする
#
# 動かし方：python app.py
# 必要なもの：ffmpeg、tkinterdnd2（ドラッグ＆ドロップ用。入れ方：python -m pip install tkinterdnd2）

import math
import queue
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

# 結果ごとの文字の色
STATUS_COLORS = {CUT: "#1a7f37", SKIP: "#666666", ERROR: "#cf222e"}


class App:
    def __init__(self, root):
        self.root = root
        self.files = []  # 一覧に入っているファイル（Path）
        self.busy = False  # 処理中かどうか（処理中は、ドラッグ＆ドロップを受け付けない）
        # 処理は別の流れ（スレッド）で動かすので、進み具合を画面に伝えるための受け渡し場所
        self.messages = queue.Queue()

        root.title("SE頭無音カット")
        root.minsize(640, 420)

        frame = tk.Frame(root, padx=16, pady=12)
        frame.pack(fill="both", expand=True)

        # 説明
        tk.Label(
            frame,
            text=f"SE の頭の無音だけを切ります（最後の無音は残します）\n"
                 f"元のファイルはそのまま残し、切ったものは同じ場所の「{OUTPUT_FOLDER}」フォルダに保存します\n"
                 "ファイルやフォルダは、この画面にドラッグ＆ドロップしても入れられます",
            justify="left", anchor="w",
        ).pack(fill="x", pady=(0, 10))

        # 選ぶボタン
        buttons = tk.Frame(frame)
        buttons.pack(fill="x")
        self.folder_button = tk.Button(buttons, text="フォルダを選ぶ", width=14, command=self.choose_folder)
        self.folder_button.pack(side="left")
        self.file_button = tk.Button(buttons, text="ファイルを選ぶ", width=14, command=self.choose_files)
        self.file_button.pack(side="left", padx=(8, 0))
        self.clear_button = tk.Button(buttons, text="一覧を空にする", width=14, command=self.clear)
        self.clear_button.pack(side="right")

        # 一覧（ファイル名・結果・理由の3列の表）
        table_frame = tk.Frame(frame)
        table_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.table = ttk.Treeview(table_frame, columns=("name", "status", "message"), show="headings", height=12)
        self.table.heading("name", text="ファイル名")
        self.table.heading("status", text="結果")
        self.table.heading("message", text="くわしく")
        self.table.column("name", width=220)
        self.table.column("status", width=70, anchor="center")
        self.table.column("message", width=470)
        for status, color in STATUS_COLORS.items():
            self.table.tag_configure(status, foreground=color)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # 設定（無音の基準・余白）
        settings = tk.Frame(frame)
        settings.pack(fill="x", pady=(10, 0))
        tk.Label(settings, text="無音とみなす大きさ").pack(side="left")
        self.db_var = tk.StringVar(value=str(DEFAULT_THRESHOLD_DB))
        tk.Entry(settings, textvariable=self.db_var, width=6, justify="right").pack(side="left", padx=(4, 0))
        tk.Label(settings, text="dB 以下").pack(side="left", padx=(2, 20))
        tk.Label(settings, text="音の直前に残す余白").pack(side="left")
        self.margin_var = tk.StringVar(value=str(DEFAULT_MARGIN_MS))
        tk.Entry(settings, textvariable=self.margin_var, width=6, justify="right").pack(side="left", padx=(4, 0))
        tk.Label(settings, text="ミリ秒").pack(side="left", padx=(2, 0))

        # 実行ボタンと、進み具合の棒
        run_row = tk.Frame(frame)
        run_row.pack(fill="x", pady=(10, 0))
        self.run_button = tk.Button(run_row, text="頭の無音をカット", width=18, command=self.start)
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(run_row, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(10, 0))

        # 状態の表示（何個選んだか、進み具合、まとめ、まちがいの知らせ）
        self.status_label = tk.Label(frame, text="", anchor="w", justify="left")
        self.status_label.pack(fill="x", pady=(6, 0))

        self.refresh()
        self.register_drop(root)

    # ---- ドラッグ＆ドロップ ----
    def register_drop(self, widget):
        """widget とその中の部品すべてで、ドラッグ＆ドロップを受け取れるようにする"""
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self.on_drop)
        for child in widget.winfo_children():
            self.register_drop(child)

    def on_drop(self, event):
        """ファイルやフォルダが落とされたとき：フォルダと音声ファイルだけを一覧に足す"""
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
            self.table.insert("", "end", values=(f.name, "", str(f.parent)))
        self.progress["value"] = 0
        self.status_label.config(text=message or f"{len(self.files)} 個のファイルが選ばれています", fg="black")
        # ファイルを選ぶまでは、実行ボタンを押せないようにする
        self.run_button.config(state="normal" if self.files else "disabled")

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
                self.status_label.config(text=f"終わりました：{summary(results)}")
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
        state = "disabled" if busy else "normal"
        for button in (self.folder_button, self.file_button, self.clear_button, self.run_button):
            button.config(state=state)


if __name__ == "__main__":
    root = TkinterDnD.Tk()  # ドラッグ＆ドロップを受け取れる画面
    App(root)
    root.mainloop()
