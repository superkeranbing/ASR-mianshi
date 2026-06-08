import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getRecording, getRecordingSummary, getRecordingQA, exportRecording, exportRecordingQA, updateTranscript } from "../services/api";
import type { Recording, Transcript, ConversationSummary, ConversationQA } from "../types";
import AudioPlayer, { type AudioPlayerHandle } from "../components/AudioPlayer";
import {
  ArrowLeft, Loader2, Search, Bookmark, BookmarkPlus,
  X, Edit3, Check, Pencil, MessageSquare, ListChecks
} from "lucide-react";

function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
}

interface BookmarkEntry {
  time: number;
  label: string;
}

export default function RecordingPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const playerRef = useRef<AudioPlayerHandle>(null);

  const [rec, setRec] = useState<Recording | null>(null);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [bookmarks, setBookmarks] = useState<BookmarkEntry[]>([]);
  const [editMode, setEditMode] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [duration, setDuration] = useState(0);

  // Summary & QA state (pre-generated, cached in DB)
  const [activeTab, setActiveTab] = useState<"summary" | "qa">("summary");
  const [summary, setSummary] = useState<ConversationSummary | null>(null);
  const [qa, setQa] = useState<ConversationQA | null>(null);

  useEffect(() => {
    if (!id) return;
    getRecording(id).then((r) => { setRec(r); setTranscripts(r.transcripts || []); }).catch(() => navigate("/"));
  }, [id, navigate]);

  // Load pre-generated summary and QA (already cached in DB, no loading state needed)
  useEffect(() => {
    if (!id) return;
    getRecordingSummary(id).then(setSummary).catch(() => {});
    getRecordingQA(id).then(setQa).catch(() => {});
  }, [id]);

  // Seek audio when transcript clicked
  const seekTo = (time: number) => playerRef.current?.seekTo(time);

  // Search filter
  const filtered = searchQuery
    ? transcripts.filter((t) => t.content.toLowerCase().includes(searchQuery.toLowerCase()))
    : transcripts;

  // Highlight search matches
  const highlight = (text: string) => {
    if (!searchQuery) return text;
    const idx = text.toLowerCase().indexOf(searchQuery.toLowerCase());
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <mark className="bg-yellow-500/30 text-yellow-200 rounded px-0.5">{text.slice(idx, idx + searchQuery.length)}</mark>
        {text.slice(idx + searchQuery.length)}
      </>
    );
  };

  // Bookmarks
  const addBookmark = (time: number, label: string) => {
    setBookmarks((prev) => [...prev, { time, label }]);
  };
  const removeBookmark = (idx: number) => {
    setBookmarks((prev) => prev.filter((_, i) => i !== idx));
  };

  // Edit transcript
  const startEdit = (t: Transcript) => {
    setEditingId(t.id);
    setEditText(t.content);
  };
  const saveEdit = async (t: Transcript) => {
    try {
      await updateTranscript(t.id, editText);
      setTranscripts((prev) => prev.map((x) => (x.id === t.id ? { ...x, content: editText } : x)));
    } catch { /* ignore */ }
    setEditingId(null);
    setEditText("");
  };

  const bookmarkMarkers = bookmarks.map((b) => ({
    time: b.time,
    label: b.label,
    color: "#f59e0b",
  }));

  if (!rec) return <div className="p-12 text-center text-gray-500">加载中...</div>;

  const audioUrl = id ? "/api/recordings/" + id + "/audio" : "";
  const showTranscriptExports = activeTab === "summary";
  const showQaExports = activeTab === "qa";

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <button onClick={() => navigate("/")} className="text-gray-500 hover:text-gray-300 transition-colors"><ArrowLeft className="w-5 h-5" /></button>
        <h2 className="text-xl font-bold">{rec.title}</h2>
        <span className={"text-xs px-2 py-1 rounded " + (rec.status === "completed" ? "bg-emerald-900/50 text-emerald-400" : "bg-yellow-900/50 text-yellow-400")}>
          {rec.status === "completed" ? "已完成" : "处理中"}
        </span>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        {[
          { label: "音频时长", value: rec.audio_duration ? Math.floor(rec.audio_duration) + "秒" : "--" },
          { label: "转写段落", value: transcripts.length },
          { label: "说话人数", value: new Set(transcripts.map((t) => t.speaker)).size },
        ].map((s) => (
          <div key={s.label} className="bg-gray-900 rounded-lg p-3 border border-gray-800 text-center">
            <p className="text-xl font-bold text-emerald-400">{s.value}</p>
            <p className="text-xs text-gray-500 mt-0.5">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Audio Player */}
      <div className="mb-4">
        <AudioPlayer ref={playerRef} audioUrl={audioUrl} onTimeUpdate={setCurrentTime} markers={bookmarkMarkers} onReady={setDuration} />
      </div>

      {/* Summary / QA Tabs */}
      <div className="mb-4">
        <div className="flex flex-wrap items-center gap-2 bg-gray-900 rounded-lg p-1 border border-gray-800 w-fit">
          <button
            onClick={() => setActiveTab("summary")}
            className={"px-4 py-2 rounded text-sm font-medium transition-colors flex items-center gap-1.5 " + (activeTab === "summary" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-gray-200")}
          >
            <MessageSquare className="w-4 h-4" />
            摘要
          </button>
          <button
            onClick={() => setActiveTab("qa")}
            className={"px-4 py-2 rounded text-sm font-medium transition-colors flex items-center gap-1.5 " + (activeTab === "qa" ? "bg-emerald-600 text-white" : "text-gray-400 hover:text-gray-200")}
          >
            <ListChecks className="w-4 h-4" />
            问答
          </button>
          {showQaExports && (
            <div className="flex items-center gap-1 pl-1 border-l border-gray-800">
              <button onClick={() => id && exportRecordingQA(id, "txt")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export QA TXT">
                QA TXT
              </button>
              <button onClick={() => id && exportRecordingQA(id, "docx")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export QA DOCX">
                QA DOCX
              </button>
              <button onClick={() => id && exportRecordingQA(id, "pdf")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export QA PDF">
                QA PDF
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Summary Content */}
      {activeTab === "summary" && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-6 mb-6">
          {summary ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-300 leading-relaxed">{summary.summary}</p>
              {summary.topics && summary.topics.length > 0 && (
                <div>
                  <h4 className="text-xs text-emerald-400 font-medium mb-2">主要话题</h4>
                  <div className="flex flex-wrap gap-1.5">
                    {summary.topics.map((t, i) => (
                      <span key={i} className="text-xs px-2.5 py-1 bg-gray-800 rounded text-gray-300">{t}</span>
                    ))}
                  </div>
                </div>
              )}
              {summary.key_points && summary.key_points.length > 0 && (
                <div>
                  <h4 className="text-xs text-emerald-400 font-medium mb-2">关键要点</h4>
                  <ul className="space-y-1.5">
                    {summary.key_points.map((p, i) => (
                      <li key={i} className="text-sm text-gray-400 flex gap-2">
                        <span className="text-emerald-400 mt-0.5">&bull;</span>
                        {p}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500 text-center py-8">暂无摘要内容</p>
          )}
        </div>
      )}

      {/* QA Content */}
      {activeTab === "qa" && (
        <div className="mb-6">
          {qa && qa.qa_pairs && qa.qa_pairs.length > 0 ? (
            <div className="space-y-3">
              {qa.qa_pairs.map((pair, i) => (
                <div key={i} className="bg-gray-900 rounded-lg border border-gray-800 p-5">
                  <div className="flex gap-3">
                    <span className="text-xs font-medium text-emerald-400 mt-0.5 shrink-0">Q{i + 1}</span>
                    <p className="text-sm text-gray-200 font-medium">{pair.question}</p>
                  </div>
                  <div className="flex gap-3 mt-3 pt-3 border-t border-gray-800">
                    <span className="text-xs font-medium text-blue-400 mt-0.5 shrink-0">A</span>
                    <p className="text-sm text-gray-400 leading-relaxed">{pair.answer}</p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="bg-gray-900 rounded-lg border border-gray-800 p-8 text-center">
              <p className="text-sm text-gray-500">暂无问答内容</p>
            </div>
          )}
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索转写文本…"
            className="w-full pl-9 pr-8 py-2 bg-gray-800 rounded border border-gray-700 text-sm text-white outline-none focus:border-emerald-500 transition-colors"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"><X className="w-3.5 h-3.5" /></button>
          )}
        </div>

        {/* Edit toggle */}
        <button
          onClick={() => setEditMode(!editMode)}
          className={"px-3 py-2 rounded text-sm transition-colors flex items-center gap-1.5 " + (editMode ? "bg-emerald-600 text-white" : "bg-gray-800 text-gray-400 hover:text-gray-200 border border-gray-700")}
        >
          <Edit3 className="w-3.5 h-3.5" /> {editMode ? "退出编辑" : "编辑模式"}
        </button>
        <div className="flex gap-1">
          {showTranscriptExports && (
            <>
              <button onClick={() => id && exportRecording(id, "txt")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export Transcript TXT">
                TXT
              </button>
              <button onClick={() => id && exportRecording(id, "srt")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export Transcript SRT">
                SRT
              </button>
              <button onClick={() => id && exportRecording(id, "docx")} className="px-2.5 py-2 bg-gray-800 hover:bg-gray-700 rounded text-xs transition-colors" title="Export Transcript DOCX">
                DOCX
              </button>
            </>
          )}
        </div>
      </div>

      {/* Bookmarks */}
      {bookmarks.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Bookmark className="w-3.5 h-3.5 text-yellow-500" />
          <span className="text-xs text-gray-500">标记:</span>
          {bookmarks.map((b, i) => (
            <span key={i} className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-yellow-900/30 text-yellow-400 rounded cursor-pointer hover:bg-yellow-900/50 transition-colors" onClick={() => seekTo(b.time)}>
              {b.label} ({fmtTime(b.time)})
              <button onClick={(e) => { e.stopPropagation(); removeBookmark(i); }} className="ml-1 hover:text-red-400"><X className="w-3 h-3" /></button>
            </span>
          ))}
        </div>
      )}

      {/* Transcripts */}
      <div className="bg-gray-900 rounded-lg border border-gray-800">
        <div className="px-6 py-3 border-b border-gray-800 font-semibold text-sm flex items-center justify-between">
          <span>📝 转写文本 {searchQuery && <span className="text-gray-500 font-normal ml-2">({filtered.length}/{transcripts.length})</span>}</span>
        </div>
        <div className="divide-y divide-gray-800 max-h-[50vh] overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="p-12 text-center text-gray-500">{searchQuery ? "没有匹配的文本" : "暂无转写内容"}</div>
          ) : (
            filtered.map((t) => {
              const isActive = currentTime >= t.start_time && currentTime < t.end_time;
              return (
                <div
                  key={t.id}
                  className={"px-6 py-3 flex gap-4 transition-colors " + (isActive ? "bg-emerald-900/10 border-l-2 border-emerald-500" : "hover:bg-gray-800/30")}
                >
                  {/* Speaker avatar */}
                  <div className="flex-shrink-0 pt-0.5">
                    <span className={"inline-flex w-8 h-8 rounded-full items-center justify-center text-xs font-medium " + (t.speaker === "面试官" ? "bg-blue-900/50 text-blue-400" : "bg-emerald-900/50 text-emerald-400")}>
                      {(t.speaker_name || t.speaker)[0]}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm">{t.speaker_name || t.speaker}</span>
                      <button onClick={() => seekTo(t.start_time)} className="text-xs text-gray-500 hover:text-emerald-400 transition-colors cursor-pointer font-mono">
                        {fmtTime(t.start_time)}
                      </button>
                      <span className="text-xs px-1.5 py-0.5 bg-gray-800 rounded text-gray-500">{t.speaker}</span>
                      {editMode && (
                        <button onClick={() => startEdit(t)} className="ml-auto text-gray-600 hover:text-gray-300 transition-colors"><Pencil className="w-3.5 h-3.5" /></button>
                      )}
                      <button onClick={() => addBookmark(t.start_time, (t.speaker_name || t.speaker).substring(0, 4) + " " + fmtTime(t.start_time))} className="text-gray-600 hover:text-yellow-400 transition-colors"><BookmarkPlus className="w-3.5 h-3.5" /></button>
                    </div>
                    {editingId === t.id ? (
                      <div className="flex gap-2">
                        <textarea value={editText} onChange={(e) => setEditText(e.target.value)} className="flex-1 bg-gray-800 border border-gray-600 rounded p-2 text-sm text-white resize-none outline-none focus:border-emerald-500" rows={2} />
                        <button onClick={() => saveEdit(t)} className="self-start p-2 bg-emerald-600 hover:bg-emerald-500 rounded transition-colors"><Check className="w-4 h-4" /></button>
                      </div>
                    ) : (
                      <p className="text-sm text-gray-300 leading-relaxed">{highlight(t.content)}</p>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
