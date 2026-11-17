# SE頭無音カット
# ステップ1：画面なしで、1つの音声ファイルの「頭の無音」だけを切る（最後の無音は残す）
# ステップ2：複数のファイルをまとめて処理する。切る必要がないファイルは、何もせずスキップする
# ステップ4：おかしなファイル（壊れている、音声でない、保存できない）を、理由を書いた「エラー」にする
#
# しくみ
#   1. ffmpeg の silencedetect（無音の場所を見つける機能）で、最初の音が基準の大きさを超えた時刻を調べる
#   2. その時刻から「余白」だけ手前を開始位置にして、ffmpeg の atrim（音の一部分を取り出す機能）で切り出す
#   3. 元のファイルは残し、元と同じ場所の「cut」フォルダに、同じ名前で保存する
#
# スキップするファイル（元のファイルにも cut フォルダにも、何もしない）
#   - 頭の無音が、余白より短い（切る必要がない）
#   - 最後まで無音（音が見つからない）
#   - cut フォルダに、同じ名前のファイルがすでにある（上書きしない）
#
# 使い方（確認用）
#   python trim_head.py ファイルかフォルダ [ファイルかフォルダ ...] [--db 基準dB] [--margin 余白ミリ秒]
#   例）python trim_head.py 爆発.wav 足音.mp3
#   例）python trim_head.py C:\SE素材 --db -60 --margin 5

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_THRESHOLD_DB = -50  # これより小さい音は「無音」とみなす（dB：音の大きさの単位。0 が最大）
DEFAULT_MARGIN_MS = 10      # 音が始まる直前に残す余白（ミリ秒：1000分の1秒）
OUTPUT_FOLDER = "cut"       # 切ったファイルを入れるフォルダの名前

# フォルダを選んだときに、処理の対象にする音声ファイルの種類（拡張子：ファイル名の最後の「.wav」など）
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aif", ".aiff"}

# silencedetect が「無音」と認める、いちばん短い長さ（秒）
# とても短くして、頭の無音がわずかでも見つけられるようにする
MIN_SILENCE_SEC = 0.001

# 処理の結果の種類
CUT = "カット"
SKIP = "スキップ"
ERROR = "エラー"

# exe にしたアプリ（黒い画面なし）から ffmpeg を動かすと、そのたびに黒い画面が一瞬出てしまう
# Windows では CREATE_NO_WINDOW を付けて、黒い画面を出さないようにする
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FfmpegMissing(RuntimeError):
    """ffmpeg・ffprobe そのものが見つからないときのエラー
    これが起きると、どのファイルも処理できないので、まとめて処理するときも、そこで止める"""


class FileProblem(ValueError):
    """ファイルそのものに問題があって、処理できないときのエラー（理由は使う人に向けた言葉で書く）"""


def run(command):
    """ffmpeg・ffprobe を動かして、結果を返す（日本語の名前のファイルでも大丈夫なように、文字は UTF-8 で受け取る）"""
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise FfmpegMissing(f"{command[0]} が見つかりません。ffmpeg を入れてから、もう一度試してください")


def probe(path):
    """ffprobe（ファイルの中身を調べる道具）で、長さ・音の形式・ビットレートを調べる
    音声として読めないときは、理由を書いた FileProblem を出す"""
    if not Path(path).is_file():
        raise FileProblem("ファイルが見つかりません（選んだあとに、消えたか名前が変わったかもしれません）")
    result = run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                  "-show_entries", "stream=codec_name,bit_rate:format=duration",
                  "-of", "default=noprint_wrappers=1", str(path)])
    if result.returncode != 0:
        raise FileProblem("音声ファイルとして読めません（壊れているか、音声ではないファイルです）")
    info = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not info.get("codec_name"):
        raise FileProblem("音声が入っていません")
    try:
        duration = float(info.get("duration", ""))
    except ValueError:
        duration = 0.0
    if duration <= 0:
        raise FileProblem("音の長さが 0 秒です（中身がないか、壊れています）")
    return {
        "duration": duration,                  # 長さ（秒）
        "codec": info["codec_name"],           # 音の形式（pcm_s16le、mp3 など）
        "bit_rate": info.get("bit_rate", ""),  # ビットレート（1秒あたりのデータ量）
    }


def find_head_silence(path, threshold_db=DEFAULT_THRESHOLD_DB):
    """頭の無音の長さ（秒）を返す。全部が無音なら None を返す

    silencedetect は、無音が始まると「silence_start: 時刻」、終わると「silence_end: 時刻」を書き出す
    いちばん最初の無音が 0 秒から始まっていれば、それが「頭の無音」で、silence_end が音の始まり
    ffmpeg が失敗したのに「無音が見つからなかった」と取りちがえないよう、先に音声として読めるか確かめる"""
    duration = probe(path)["duration"]
    result = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                  "-af", f"silencedetect=noise={threshold_db}dB:d={MIN_SILENCE_SEC}",
                  "-f", "null", "-"])  # -f null -：ファイルは作らず、調べるだけ
    if result.returncode != 0:
        raise FileProblem("音を最後まで読めませんでした（ファイルが壊れているかもしれません）")
    log = result.stderr

    first_start = re.search(r"silence_start: (-?[\d.]+)", log)
    if not first_start or float(first_start.group(1)) > MIN_SILENCE_SEC:
        return 0.0  # 頭からすぐ音が鳴っている（頭の無音なし）

    first_end = re.search(r"silence_end: ([\d.]+)", log)
    if not first_end or float(first_end.group(1)) >= duration - MIN_SILENCE_SEC:
        return None  # 最後まで無音が続いている（全部が無音）
    return float(first_end.group(1))


def codec_options(info):
    """元のファイルと同じ音質で保存するための、ffmpeg の指定を返す"""
    codec = info["codec"]
    if codec.startswith("pcm_") or codec == "flac":
        return ["-c:a", codec]  # WAV・FLAC：元と同じ形式（16bit・24bit など）のまま。音質は落ちない
    if codec == "mp3" and info["bit_rate"].isdigit():
        return ["-c:a", "libmp3lame", "-b:a", info["bit_rate"]]  # MP3：元と同じビットレートで作り直す
    return []  # そのほかの形式は、ffmpeg のおまかせ


def make_output_folder(folder):
    """cut フォルダを作る。作れないときは、理由を使う人の言葉で伝える"""
    if folder.exists() and not folder.is_dir():
        raise FileProblem(f"「{folder.name}」という名前のファイルがあるため、{folder.name} フォルダを作れません")
    try:
        folder.mkdir(exist_ok=True)
    except PermissionError:
        raise FileProblem("この場所には保存できません（書き込みが許可されていない場所です）")
    except OSError as e:
        raise FileProblem(f"{folder.name} フォルダを作れませんでした（{e.strerror}）")


def cut_file(path, out_path, start):
    """path の start 秒より前を捨てて、out_path に保存する
    失敗したときは、今回作りかけたファイルだけを消す（呼ぶ前に「同じ名前はない」と確かめているので、
    消すのは必ず今回作ったもの。元のファイルには触れない）"""
    options = codec_options(probe(path))
    make_output_folder(out_path.parent)
    command = ["ffmpeg", "-hide_banner", "-n", "-i", str(path),  # -n：同じ名前があれば上書きしない（念のため）
               "-map", "0:a:0", "-map_metadata", "0",            # 音だけを使い、曲名などの情報は引き継ぐ
               "-af", f"atrim=start={start:.6f},asetpts=PTS-STARTPTS",  # start 秒より前を捨てて、時刻を0から数え直す
               *options, str(out_path)]
    result = run(command)
    if result.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise FileProblem("保存に失敗しました（保存先がいっぱいか、書き込めない場所かもしれません）")


def trim_head(path, threshold_db=DEFAULT_THRESHOLD_DB, margin_ms=DEFAULT_MARGIN_MS):
    """1つのファイルを処理して、結果を {"file", "status", "message"} で返す
    status は CUT（切った）・SKIP（何もしなかった）のどちらか"""
    path = Path(path)
    out_path = path.parent / OUTPUT_FOLDER / path.name

    # 同じ名前が cut フォルダにあるなら、調べる前に飛ばす（上書きしない）
    if out_path.exists():
        return {"file": path, "status": SKIP, "message": f"{OUTPUT_FOLDER} フォルダに同じ名前のファイルがあります"}

    silence = find_head_silence(path, threshold_db)
    if silence is None:
        return {"file": path, "status": SKIP, "message": "最後まで無音です（基準の dB を小さくすると、音として見つかるかもしれません）"}

    if silence == 0:
        return {"file": path, "status": SKIP, "message": "頭に無音がありません"}

    margin = margin_ms / 1000
    if silence <= margin:
        return {"file": path, "status": SKIP, "message": f"頭の無音が {silence:.3f} 秒で、余白より短いので切る必要がありません"}

    start = silence - margin  # 余白のぶんだけ手前から始める
    cut_file(path, out_path, start)
    return {"file": path, "status": CUT, "message": f"{start:.3f} 秒切りました"}


def collect_files(paths):
    """ファイルとフォルダが混ざったリストから、処理する音声ファイルの一覧を作る
    フォルダは、その中（すぐ下だけ。cut フォルダなどの中は見ない）の音声ファイルを名前順に入れる
    同じファイルが2回入らないようにする"""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)
        else:
            files.append(p)
    unique = []
    for f in files:
        if f.resolve() not in {u.resolve() for u in unique}:
            unique.append(f)
    return unique


def trim_many(files, threshold_db=DEFAULT_THRESHOLD_DB, margin_ms=DEFAULT_MARGIN_MS, on_progress=None):
    """たくさんのファイルを順番に処理して、結果のリストを返す
    1つが失敗しても止まらず、そのファイルを ERROR にして次へ進む
    ただし ffmpeg が見つからないときは、どのファイルも処理できないので、FfmpegMissing を出してそこで止める
    on_progress があれば、1つ終わるたびに (終わった数, 全部の数, 結果) を知らせる（画面の進み具合用）"""
    results = []
    for i, f in enumerate(files, start=1):
        try:
            result = trim_head(f, threshold_db, margin_ms)
        except FfmpegMissing:
            raise
        except FileProblem as e:
            result = {"file": Path(f), "status": ERROR, "message": str(e)}
        except Exception as e:  # 考えていなかった失敗も、そのファイルだけのエラーにして先へ進む
            result = {"file": Path(f), "status": ERROR, "message": f"思わぬエラーが起きました（{e}）"}
        results.append(result)
        if on_progress:
            on_progress(i, len(files), result)
    return results


def summary(results):
    """「カット 3個、スキップ 2個、エラー 0個」のようなまとめの文を作る"""
    count = {s: sum(r["status"] == s for r in results) for s in (CUT, SKIP, ERROR)}
    return "、".join(f"{s} {n}個" for s, n in count.items())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SE の頭の無音だけを切る")
    parser.add_argument("paths", nargs="+", help="音声ファイルか、音声ファイルが入ったフォルダ")
    parser.add_argument("--db", type=float, default=DEFAULT_THRESHOLD_DB, help="無音とみなす大きさ（dB）")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN_MS, help="音の直前に残す余白（ミリ秒）")
    args = parser.parse_args()

    files = collect_files(args.paths)
    try:
        results = trim_many(files, args.db, args.margin,
                            on_progress=lambda i, n, r: print(f"[{i}/{n}] {r['status']}：{r['file'].name}　{r['message']}"))
    except FfmpegMissing as e:
        print(e)
        sys.exit(1)
    print("---")
    print(summary(results))
