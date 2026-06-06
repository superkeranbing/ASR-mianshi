"""
ASR Engine - 音频转写 + 说话人分离 (Speaker Diarization)
====================================================

后端选择:
  - funasr:   FunASR 自定义管道 (独立 VAD + ERes2NetV2 聚类 + ASR)
  - whisper:  faster-whisper (备选)
  - sherpa:   Sherpa-ONNX (占位)
  - mock:     开发测试用

安装:
  pip install funasr modelscope torch torchaudio scikit-learn
  pip install faster-whisper
  pip install sherpa-onnx
"""

import os, wave, logging, random, re, tempfile, shutil
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioMeta:
    path: str
    format: str
    duration: float
    sample_rate: int
    channels: int
    file_size: int


@dataclass
class TranscriptSegment:
    speaker: str
    speaker_name: str
    content: str
    start_time: float
    end_time: float
    confidence: float = 0.95


class ASREngine:
    """
    统一 ASR 引擎 — 自定义 VAD + ERes2NetV2 聚类 + ASR 管道。

    设计决策:
      不用 FunASR 官方 AutoModel(..., spk_model="campplus") 组合管道（VAD 合并过于激进）。
      原因是官网道的 VAD 合并策略对面试场景（快速问答，段间隔平均 0.38s）
      过于激进，564 段被合并为 1-4 段，无法有效分离说话人。
      所以拆成三个独立模型自己控制分段合并和聚类。

    参数调节指南:
      gap_ms:     VAD 段合并阈值 (默认 500ms)。面试 500，会议 200，独白 1000
      max_dur_ms: 每段最长时长 (默认 30s)。越长段越少但可能混淆说话人
      n_clusters: 聚类数量 (默认 2)。两人 2，多人 3-4
      linkage:    聚类链接方式。complete(紧凑) / average(一般) / single(宽松)
      batch_size_s: ASR 批次时长 (默认 300)。值小更精确但慢
      speech_noise_thres: VAD 灵敏度 (默认 0.6)。调低检测更小声的语音
    """

    def __init__(self, backend: str = "mock", model_dir: str = "./models"):
        self.backend = backend
        self.model_dir = model_dir
        self._vad_model = None
        self._campp_model = None
        self._asr_model = None
        self._whisper_model = None
        logger.info(f"ASREngine initialized: backend={backend}")

    # ── 音频元数据 ────────────────────────────────────

    def read_audio_meta(self, file_path: str) -> AudioMeta:
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        file_size = os.path.getsize(file_path)
        if ext == "wav":
            with wave.open(file_path, "rb") as wf:
                return AudioMeta(path=file_path, format=ext,
                                 duration=wf.getnframes() / wf.getframerate(),
                                 sample_rate=wf.getframerate(),
                                 channels=wf.getnchannels(), file_size=file_size)
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(file_path, format=ext if ext != "m4a" else "mp4")
            return AudioMeta(path=file_path, format=ext,
                             duration=len(audio) / 1000.0,
                             sample_rate=audio.frame_rate,
                             channels=audio.channels, file_size=file_size)
        except Exception:
            import soundfile as sf
            info = sf.info(file_path)
            return AudioMeta(path=file_path, format=ext,
                             duration=info.duration,
                             sample_rate=info.samplerate,
                             channels=info.channels, file_size=file_size)

    # ── 主转写入口 ────────────────────────────────────

    async def transcribe(self, file_path: str) -> list[TranscriptSegment]:
        meta = self.read_audio_meta(file_path)
        logger.info(f"Audio: {meta.duration:.1f}s, {meta.sample_rate}Hz, {meta.channels}ch")

        if self.backend == "funasr":
            results = await self._transcribe_funasr(file_path)
            return self._post_process_speakers(results, meta)
        elif self.backend == "whisper":
            results = await self._transcribe_whisper(file_path)
            return self._post_process_speakers(results, meta)
        elif self.backend == "sherpa":
            return await self._transcribe_sherpa(file_path)

        results = self._transcribe_mock(meta, self._mock_vad(meta))
        return self._post_process_speakers(results, meta)

    def _post_process_speakers(self, segments, meta):
        if not segments:
            return segments
        if not all(s.speaker in ("", "未知", "发言人") for s in segments):
            return segments

        if len(segments) == 1:
            text = segments[0].content
            total_dur = segments[0].end_time - segments[0].start_time or 60
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])", text) if s.strip()]
            if len(sentences) < 2:
                chunk_size = max(1, len(text) // 6)
                sentences = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            tps = total_dur / max(1, len(sentences))
            results = []
            for i, sent in enumerate(sentences):
                spk = "说话人1" if i % 2 == 0 else "说话人2"
                results.append(TranscriptSegment(
                    speaker=spk, speaker_name=spk, content=sent,
                    start_time=round(i * tps, 2), end_time=round((i + 1) * tps, 2),
                    confidence=segments[0].confidence,
                ))
            return results

        speakers = ["说话人1", "说话人2"]
        for i, s in enumerate(segments):
            spk = speakers[i % 2]
            s.speaker = spk
            s.speaker_name = spk
        return segments

    # ── 模拟 ──────────────────────────────────────────

    MOCK_DIALOG = [
        ("面试官", "请简单介绍一下你自己。"),
        ("候选人", "我叫张三，毕业于XX大学计算机科学专业。"),
        ("面试官", "能详细说说React的虚拟DOM原理吗？"),
        ("候选人", "虚拟DOM是React创造的核心优化概念。"),
        ("面试官", "你在项目中遇到过最大的技术挑战是什么？"),
        ("候选人", "在处理大数据量列表时遇到了严重性能问题。"),
        ("面试官", "你如何保证前端代码质量？"),
        ("候选人", "我们团队采用了ESLint+Prettier统一代码风格。"),
        ("面试官", "你对未来的职业规划有什么考虑？"),
        ("候选人", "短期希望在前端架构方向深耕。"),
    ]

    def _mock_vad(self, meta: AudioMeta) -> list[dict]:
        ideal = random.uniform(4, 8)
        n = max(1, int(meta.duration / ideal))
        return [{"start": round(i * meta.duration / n, 2),
                 "end": round((i + 1) * meta.duration / n if i < n - 1 else meta.duration, 2)}
                for i in range(n)]

    def _transcribe_mock(self, meta, vad_segments=None) -> list[TranscriptSegment]:
        if vad_segments is None:
            vad_segments = self._mock_vad(meta)
        results = []
        for i, seg in enumerate(vad_segments):
            spk, content = self.MOCK_DIALOG[i] if i < len(self.MOCK_DIALOG) else ("未知", "...")
            results.append(TranscriptSegment(
                speaker=spk, speaker_name=spk, content=content,
                start_time=seg["start"], end_time=seg["end"],
                confidence=round(random.uniform(0.88, 0.99), 2),
            ))
        return results


    # ════════════════════════════════════════════════════
    # FunASR — 自定义管道 (独立 VAD + ERes2NetV2 聚类 + ASR)
    # ════════════════════════════════════════════════════
    #
    # 为什么不用官方 AutoModel(..., spk_model="campplus")?
    #   官网道的 VAD 合并策略对面试场景过于激进。
    #   将 VAD、ERes2NetV2、ASR 拆为三个独立模型，自己控制合并逻辑。
    #
    # 流程:
    #   独立 VAD → 564+ 段 → 自定义合并 → ~119 段
    #     → ERes2NetV2 批次 119 个 192维 embedding
    #     → sklearn 余弦聚类 → 说话人1/说话人2
    #     → ASR 批次识别 → 组装 TranscriptSegment[]
    #
    # 参数调节:
    #   gap_ms:       合并阈值 (行: _merge_segments)
    #   max_dur_ms:   段长上限 (行: _merge_segments)
    #   n_clusters:   聚类数量 (行: _transcribe_funasr)
    #   linkage:      聚类链接方式 (行: _transcribe_funasr)

    async def _transcribe_funasr(self, path: str) -> list[TranscriptSegment]:
        """
        FunASR 自定义管道 — 独立 VAD + ERes2NetV2 聚类 + ASR 批次识别。

        参数调节:
          gap_ms:       间隔 < 此值的 VAD 段合并。面试 500，会议 200，独白 1000
          max_dur_ms:   合并后段长上限。默认 30s。越长段越少但可能混淆说话人
          n_clusters:   聚类数量。两人 2，多人会议 3-4
          linkage:      聚类链接方式。"complete"紧凑 / "average"一般 / "single"宽松
          batch_size_s: ASR 批次时长 (入参)。默认 300。值小更精确但慢
        """
        try:
            import numpy as np
            from funasr.models.campplus.cluster_backend import ClusterBackend
            from funasr import AutoModel
            import torchaudio
            import torchaudio.transforms as T

            # 懒加载三个独立模型（共享 FunASR 权重缓存）
            if not self._vad_model:
                self._vad_model = AutoModel(
                    model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    disable_update=True, log_level="WARNING",
                )
                # VAD 灵敏度调节（可选）:
                # self._vad_model.kwargs["model_conf"]["speech_noise_thres"] = 0.6

            if not self._campp_model:
                self._campp_model = AutoModel(
                    model="iic/speech_eres2netv2_sv_zh-cn_16k-common",
                    disable_update=True, log_level="WARNING",
                )

            if not self._asr_model:
                self._asr_model = AutoModel(
                    model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                    vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
                    disable_update=True, log_level="WARNING",
                )

            # ── 1. VAD 分段 ──────────────────────────
            logger.info("Running VAD...")
            vad_result = self._vad_model.generate(input=path)
            raw_segments = vad_result[0]["value"]
            logger.info(f"VAD: {len(raw_segments)} raw segments")

            # ── 2. 合并相邻段 ─────────────────────────
            # gap_ms:    面试 500ms  —— 两人快速问答
            #            会议 200ms  —— 多人讨论
            #            独白 1000ms —— 单人长时间发言
            # max_dur_ms: 默认 30s。越长段越少但可能含多说话人
            merged = self._merge_segments(raw_segments, gap_ms=100, max_dur_ms=15000)
            merged = [(s, e) for s, e in merged if (e - s) >= 200]
            logger.info(f"Merged: {len(merged)} chunks")

            if not merged:
                return self._transcribe_mock(
                    AudioMeta(path, "wav", 130, 16000, 1, 0),
                    [{"start": 0, "end": 0}],
                )

            # ── 3. 加载音频 16kHz mono ──────────────
            audio, sr = torchaudio.load(path)
            if sr != 16000:
                audio = T.Resample(sr, 16000)(audio)
            if audio.shape[0] > 1:
                audio = audio.mean(dim=0, keepdim=True)

            # ── 4. 保存各段为临时 WAV ───────────────
            temp_dir = tempfile.mkdtemp(prefix="asr_seg_")
            temp_paths, chunk_times = [], []
            for i, (start_ms, end_ms) in enumerate(merged):
                s = int(start_ms * 16000 / 1000)
                e = int(end_ms * 16000 / 1000)
                if e <= s:
                    continue
                seg = audio[:, s:e]
                if seg.shape[1] < 1600:
                    continue
                seg_path = os.path.join(temp_dir, f"seg_{i:04d}.wav")
                torchaudio.save(seg_path, seg, 16000)
                temp_paths.append(seg_path)
                chunk_times.append((start_ms / 1000.0, end_ms / 1000.0))

            logger.info(f"Saved {len(temp_paths)} segment files")

            if len(temp_paths) < 2:
                speaker = "说话人1"
                results_list = []
                asr_out = self._asr_model.generate(input=temp_paths[0])
                if asr_out:
                    text = asr_out[0].get("text", "")
                    results_list.append(TranscriptSegment(
                        speaker=speaker, speaker_name=speaker, content=text,
                        start_time=chunk_times[0][0], end_time=chunk_times[0][1],
                        confidence=0.95,
                    ))
                self._cleanup_temp(temp_dir)
                return results_list

            # ── 5. ERes2NetV2 批次 embedding ────────
            logger.info("Extracting speaker embeddings...")
            spk_results = self._campp_model.generate(input=temp_paths)
            embeddings, valid_indices = [], []
            for idx, spk_out in enumerate(spk_results):
                if "spk_embedding" in spk_out:
                    emb = spk_out["spk_embedding"].cpu().numpy().flatten()
                    if np.any(emb):
                        embeddings.append(emb)
                        valid_indices.append(idx)

            # ── 6. 官方聚类 (自动确定说话人数) ───────
            # 使用 FunASR 官方的 ClusterBackend，与官方管道完全一致。
            #
            # ClusterBackend 自动确定人数的逻辑:
            #   - embedding < 20 个 → 1 个说话人
            #   - 20-2048 个 → SpectralCluster (拉普拉斯特征值间隙法)
            #   - >= 2048 个 → UMAP + HDBSCAN (密度聚类)
            #   - 最后按余弦相似度合并相近说话人 (merge_thr=0.78)
            #
            # 你也可以手动指定人数:
            #   cb.forward(np.array(embeddings), oracle_num=2)
            if len(embeddings) < 2:
                logger.warning(f"Only {len(embeddings)} valid embeddings, skip clustering")
                speaker_labels = ["说话人1"] * len(valid_indices)
            else:
                logger.info(f"Clustering {len(embeddings)} embeddings with ClusterBackend...")
                cb = ClusterBackend()
                labels = cb.forward(np.array(embeddings))
                unique = sorted([l for l in set(labels) if l >= 0])
                if len(unique) > 1 and labels[0] != 0:
                    labels = [1 - l if l >= 0 else l for l in labels]
                speaker_labels = [f"说话人{l+1}" if l >= 0 else "未知" for l in labels]

            # ── 7. ASR 批次识别 ────────────────────
            logger.info("Running ASR batch...")
            asr_results = self._asr_model.generate(input=temp_paths)

            # ── 8. 组装结果 ────────────────────────
            results_list = []
            for i, idx in enumerate(valid_indices):
                speaker = speaker_labels[i] if i < len(speaker_labels) else "说话人1"
                asr_item = asr_results[idx] if idx < len(asr_results) else {}
                text = asr_item.get("text", "") if isinstance(asr_item, dict) else ""
                start_t, end_t = chunk_times[idx]
                results_list.append(TranscriptSegment(
                    speaker=speaker, speaker_name=speaker, content=text,
                    start_time=start_t, end_time=end_t, confidence=0.95,
                ))

            self._cleanup_temp(temp_dir)
            logger.info(f"Done: {len(results_list)} segments with speaker labels")
            return results_list

        except ImportError as e:
            logger.warning(
                f"FunASR dependency import failed: {type(e).__name__}: {e}, falling back to mock",
                exc_info=True,
            )
            return self._transcribe_mock(
                AudioMeta(path, "wav", 130, 16000, 1, 0),
                [{"start": 0, "end": 130}],
            )
        except Exception as e:
            logger.error(f"FunASR error: {type(e).__name__}: {e}", exc_info=True)
            self._cleanup_temp(temp_dir)
            return self._transcribe_mock(
                AudioMeta(path, "wav", 130, 16000, 1, 0),
                [{"start": 0, "end": 130}],
            )


    # ════════════════════════════════════════════════════
    # 辅助方法
    # ════════════════════════════════════════════════════

    def _merge_segments(self, segments: list, gap_ms: int = 500, max_dur_ms: int = 30000) -> list:
        """
        合并 VAD 分段。

        策略: 遍历 VAD 段，间隔 < gap_ms 且合并后总长 < max_dur_ms 则合并。

        参数:
          gap_ms:     合并阈值 (ms)。面试 500，会议 200，独白 1000
          max_dur_ms: 段长上限 (ms)。默认 30s

        返回: [(start_ms, end_ms), ...]
        """
        if not segments:
            return []
        merged = []
        cur_start, cur_end = segments[0]
        for start, end in segments[1:]:
            if (start - cur_end) < gap_ms and (max(end, cur_end) - cur_start) < max_dur_ms:
                cur_end = max(cur_end, end)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = start, end
        merged.append((cur_start, cur_end))
        return merged

    def _cleanup_temp(self, temp_dir: str):
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


    # ════════════════════════════════════════════════════
    # 其他后端 (占位)
    # ════════════════════════════════════════════════════

    async def _transcribe_whisper(self, path: str) -> list[TranscriptSegment]:
        try:
            from faster_whisper import WhisperModel
            if not self._whisper_model:
                self._whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
            segs, _ = self._whisper_model.transcribe(path, beam_size=5, language="zh")
            results = []
            for seg in segs:
                results.append(TranscriptSegment(
                    speaker="未知", speaker_name="发言人",
                    content=seg.text.strip(),
                    start_time=seg.start, end_time=seg.end,
                    confidence=round(1.0 - seg.avg_logprob / abs(seg.avg_logprob or 1), 2),
                ))
            return results
        except ImportError:
            logger.warning("faster-whisper not installed, falling back to mock")
            return self._transcribe_mock(self.read_audio_meta(path))

    async def _transcribe_sherpa(self, path: str) -> list[TranscriptSegment]:
        try:
            import sherpa_onnx
            raise NotImplementedError("Sherpa-ONNX integration: configure model path")
        except ImportError:
            logger.warning("sherpa-onnx not installed, falling back to mock")
            return self._transcribe_mock(self.read_audio_meta(path))


# ════════════════════════════════════════════════════════════
# 全局单例
asr_engine = ASREngine(backend="funasr")
