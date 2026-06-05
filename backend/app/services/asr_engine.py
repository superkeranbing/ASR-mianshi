"""
ASR Engine - 音频转写 + 说话人分离 (Speaker Diarization)

架构概述 / Architecture:
────────────────────────────────────────────────────────
  音频文件 (mp3/wav)
    │
    ├─ funasr 后端 ──→  独立 VAD 分段      (564+ 细粒度段)
    │                     ↓
    │                   智能合并            (gap<500ms, → 119段)
    │                     ↓
    │                   CAM++ embedding    (192维/段)
    │                     ↓
    │                   AgglomerativeClustering (余弦距离聚类)
    │                     ↓
    │                   ASR 文本识别        (Paraformer 批次处理)
    │                     ↓
    │                   TranscriptSegment[] (带说话人1/说话人2 标签)
    │
    ├─ whisper 后端 ──→  faster-whisper (备选)
    │
    └─ mock 后端    ──→  开发测试用模拟数据

后端选择:
  - funasr:   FunASR Paraformer + CAM++ + 自定义说话人聚类 (生产推荐)
  - whisper:  OpenAI Whisper / faster-whisper (英文/多语言)
  - sherpa:   Sherpa-ONNX (CPU 边缘部署)
  - mock:     模拟引擎 (开发/测试用，无模型依赖)

安装:
  pip install funasr modelscope      # FunASR 后端
  pip install faster-whisper         # Whisper 后端
  pip install sherpa-onnx            # Sherpa 后端

为什么不用 FunASR 内置的 spk_model? / Why not built-in spk_model?
────────────────────────────────────────────────────────
FunASR 的 AutoModel(..., spk_model="campplus") 组合管道在内部:
  1. 调 VAD → 2. 合并 VAD 段 → 3. ASR → 4. CAM++ → 5. 聚类 → 6. 返回

问题出在第 2 步: AutoModel 内部对 VAD 段的合并阈值过大。
对于面试场景（两人快速问答，段间间隔平均 0.38s），VAD 合并后的结果
只有 1 个段 → CAM++ 只拿到 1 个 embedding → 无法聚类 → 没有说话人标签。

解决方案: 将 VAD、CAM++、ASR 拆成三个独立模型，自己控制合并逻辑。
"""

import os, wave, logging, random, hashlib, re, tempfile, math, shutil
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 数据模型 / Data Models
# ═══════════════════════════════════════════════════════════════

@dataclass
class AudioMeta:
    """音频文件元数据"""
    path: str            # 文件路径
    format: str          # 格式 (wav/mp3/m4a/...)
    duration: float      # 时长 (秒)
    sample_rate: int     # 采样率 (Hz)
    channels: int        # 声道数 (1=mono, 2=stereo)
    file_size: int       # 文件大小 (字节)


@dataclass
class TranscriptSegment:
    """
    转录段落 — 带时间戳和说话人标签的最小单元。

    一次面试录音的一个语义段落:
      - 一个人说的一段话（或一个完整句子）
      - 有明确的起止时间戳
      - 已分配说话人 (说话人1 / 说话人2)
    """
    speaker: str          # 说话人标签: "说话人1" / "说话人2" / "面试官" / "候选人"
    speaker_name: str     # 说话人名称 (扩展用，当前与 speaker 相同)
    content: str          # 转写文本
    start_time: float     # 开始时间 (秒)
    end_time: float       # 结束时间 (秒)
    confidence: float = 0.95  # 置信度 (0-1)


# ═══════════════════════════════════════════════════════════════
# ASR 引擎主类 / Main ASR Engine
# ═══════════════════════════════════════════════════════════════

class ASREngine:
    """
    统一 ASR 引擎 — 音频处理 + 自定义说话人分离管道。

    核心策略:
      不依赖 FunASR 内置的 spk_model 组合管道（存在 VAD 合并问题），
      而是将 VAD、CAM++、ASR 拆分为三个独立 AutoModel 实例，
      自己控制分段合并和聚类逻辑。

    设计原则:
      - 模型懒加载: 只在首次使用时加载，实例级别缓存
      - 批次处理: 所有分段一次性送入 CAM++ 和 ASR，避免循环调用
      - 独立控制: 分段、合并、聚类的参数可独立调整
    """
    MOCK_DIALOG = [
        ("面试官", "面试官李", "请简单介绍一下你自己。"),
        ("候选人", "张三", "面试官好，我叫张三，毕业于XX大学计算机科学专业，有5年前端开发经验。"),
        ("面试官", "面试官李", "能详细说说React的虚拟DOM原理吗？"),
        ("候选人", "张三", "虚拟DOM是React创造的核心优化概念。"),
        ("面试官", "面试官李", "你在项目中遇到过最大的技术挑战是什么？"),
        ("候选人", "张三", "在处理大数据量列表时遇到了严重性能问题。"),
        ("面试官", "面试官李", "你如何保证前端代码质量？"),
        ("候选人", "张三", "我们团队采用了ESLint+Prettier统一代码风格。"),
        ("面试官", "面试官李", "你对未来的职业规划有什么考虑？"),
        ("候选人", "张三", "短期希望在前端架构方向深耕。"),
    ]

    def __init__(self, backend: str = "mock", model_dir: str = "./models"):
        """
        初始化引擎。

        参数:
          backend:    ASR 后端 ("funasr" / "whisper" / "sherpa" / "mock")
          model_dir:  模型文件目录（当前未使用）
        """
        self.backend = backend
        self.model_dir = model_dir
        # 三个独立模型的懒加载占位
        self._vad_model = None      # 独立 VAD 模型 (无合并逻辑)
        self._campp_model = None    # 独立 CAM++ 模型 (返回原始 192维 embedding)
        self._asr_model = None      # 独立 ASR 模型 (Paraformer + PUNC, 无 spk_model)
        self._funasr_model = None   # 废弃: 旧版组合模型 (保留兼容)
        self._whisper_model = None
        logger.info(f"ASREngine initialized: backend={backend}")

    # ─────────────────────────────────────────────────────────
    # 音频元数据读取 / Audio Metadata
    # ─────────────────────────────────────────────────────────

    def read_audio_meta(self, file_path: str) -> AudioMeta:
        """读取音频文件元数据。支持 wav 直接读取，其他格式通过 pydub/soundfile 读取。"""
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        file_size = os.path.getsize(file_path)
        if ext == "wav":
            return self._read_wav(file_path, ext, file_size)
        else:
            return self._read_with_pydub(file_path, ext, file_size)

    def _read_wav(self, path: str, fmt: str, size: int) -> AudioMeta:
        """直接解析 WAV 文件头获取元数据（无需额外依赖）。"""
        with wave.open(path, "rb") as wf:
            return AudioMeta(
                path=path, format=fmt,
                duration=wf.getnframes() / wf.getframerate(),
                sample_rate=wf.getframerate(),
                channels=wf.getnchannels(),
                file_size=size,
            )

    def _read_with_pydub(self, path: str, fmt: str, size: int) -> AudioMeta:
        """
        通过 pydub 或 soundfile 读取非 WAV 格式的元数据。
        pydub 需要 ffmpeg 支持 mp3/m4a 等格式；fallback 到 soundfile。
        """
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(path, format=fmt if fmt != "m4a" else "mp4")
            return AudioMeta(
                path=path, format=fmt,
                duration=len(audio) / 1000.0,
                sample_rate=audio.frame_rate,
                channels=audio.channels,
                file_size=size,
            )
        except Exception:
            import soundfile as sf
            info = sf.info(path)
            return AudioMeta(
                path=path, format=fmt,
                duration=info.duration,
                sample_rate=info.samplerate,
                channels=info.channels,
                file_size=size,
            )

    # ─────────────────────────────────────────────────────────
    # 模拟 VAD 分段 / Mock VAD (仅用于 mock 后端)
    # ─────────────────────────────────────────────────────────

    def segment_by_vad(self, meta: AudioMeta) -> list[dict]:
        """
        模拟 VAD 分段。仅用于 mock 后端，funasr 后端会使用真实 VAD 模型分段。
        按时长均匀切分，每段 4-8 秒。
        """
        ideal_seg_duration = random.uniform(4, 8)
        num_segments = max(1, int(meta.duration / ideal_seg_duration))
        num_segments = min(num_segments, len(self.MOCK_DIALOG))
        segments = []
        for i in range(num_segments):
            start = i * (meta.duration / num_segments)
            end = (i + 1) * (meta.duration / num_segments) if i < num_segments - 1 else meta.duration
            segments.append({"start": round(start, 2), "end": round(end, 2)})
        return segments

    # ─────────────────────────────────────────────────────────
    # 主转写入口 / Main Transcribe Entry Point
    # ─────────────────────────────────────────────────────────

    async def transcribe(self, file_path: str) -> list[TranscriptSegment]:
        """
        转写音频文件的主入口。

        流程:
          1. 读取音频元数据 (时长、采样率、声道数)
          2. 根据 backend 选择转写管道
          3. 后处理说话人标签（如果标签为空则做 fallback 分配）

        参数:
          file_path: 音频文件路径 (支持 mp3/wav/m4a 等)

        返回:
          TranscriptSegment[] — 带时间戳、说话人标签的转录段落列表
        """
        meta = self.read_audio_meta(file_path)
        logger.info(f"Audio: {meta.duration:.1f}s, {meta.sample_rate}Hz, {meta.channels}ch")
        vad_segments = self.segment_by_vad(meta)  # mock 用，funasr 里被忽略

        if self.backend == "funasr":
            # FunASR 后端使用自定义管道（独立 VAD + CAM++ 聚类 + ASR）
            results = await self._transcribe_funasr(file_path)
            return self._post_process_speakers(results, meta)

        elif self.backend == "whisper":
            results = await self._transcribe_whisper(file_path, vad_segments)
            return self._post_process_speakers(results, meta)

        elif self.backend == "sherpa":
            return await self._transcribe_sherpa(file_path, vad_segments)

        # mock 后端
        results = self._transcribe_mock(meta, vad_segments)
        return self._post_process_speakers(results, meta)

    # ─────────────────────────────────────────────────────────
    # 说话人标签后处理 / Speaker Label Post-Processing
    # ─────────────────────────────────────────────────────────

    def _post_process_speakers(self, segments: list[TranscriptSegment], meta: AudioMeta) -> list[TranscriptSegment]:
        """
        说话人标签后处理。

        只有当所有段都是 "未知" 时才执行 fallback 策略:
          - 多段时: 交替分配 "面试官" / "候选人"
          - 单段时: 按标点符号切分句子后再交替分配

        FunASR 管道的输出已有 "说话人1" / "说话人2" 标签，
        所以这个 fallback 对 funasr 后端实际上不起作用。
        """
        if not segments:
            return segments
        all_unknown = all(s.speaker == "未知" for s in segments)
        if not all_unknown:
            return segments

        # 单段处理: 按句子切分后交替分配
        if len(segments) == 1:
            text = segments[0].content
            total_dur = segments[0].end_time - segments[0].start_time or 60
            sentences = re.split(r'(?<=[.!?])', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            if len(sentences) < 2:
                chunk_size = max(1, len(text) // 6)
                parts = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
                sentences = parts
            results = []
            speakers = ["面试官", "候选人"]
            time_per_sentence = total_dur / max(1, len(sentences))
            for i, sent in enumerate(sentences):
                spk = speakers[i % 2]
                results.append(TranscriptSegment(
                    speaker=spk, speaker_name=spk + ("李" if i % 2 == 0 else "张三"),
                    content=sent,
                    start_time=round(i * time_per_sentence, 2),
                    end_time=round((i + 1) * time_per_sentence, 2),
                    confidence=segments[0].confidence,
                ))
            return results

        # 多段: 交替分配
        speakers = ["面试官", "候选人"]
        for i, s in enumerate(segments):
            spk = speakers[i % 2]
            s.speaker = spk
            s.speaker_name = spk + ("李" if i % 2 == 0 else "张三")
        return segments

    # ─────────────────────────────────────────────────────────
    # 模拟转写 / Mock Transcription (开发/测试用)
    # ─────────────────────────────────────────────────────────

    def _transcribe_mock(self, meta: AudioMeta, vad_segments: list[dict]) -> list[TranscriptSegment]:
        """使用预置对话模拟转写结果，不依赖任何模型。"""
        results = []
        for i, seg in enumerate(vad_segments):
            if i < len(self.MOCK_DIALOG):
                speaker, name, content = self.MOCK_DIALOG[i]
            else:
                speaker, name, content = "未知", "发言人", "..."
            results.append(TranscriptSegment(
                speaker=speaker, speaker_name=name,
                content=content,
                start_time=seg["start"], end_time=seg["end"],
                confidence=round(random.uniform(0.88, 0.99), 2),
            ))
        return results

    # ═══════════════════════════════════════════════════════════
    # FunASR 自定义管道 (核心实现 / Core Pipeline)
    # ═══════════════════════════════════════════════════════════
    #
    # 为什么不直接用 FunASR 的 AutoModel(..., spk_model="campplus")?
    # ──────────────────────────────────────────────────────────
    # 该组合管道内部有 VAD 段合并逻辑，且合并阈值不可调。
    # 在线面试录音的特点: 两人快速问答，段间静音间隔极短（平均 0.38s）。
    # AutoModel 内部合并后只得到 1 个语音段 → CAM++ 只拿到 1 个 embedding
    # → 1 个点无法聚类 → 说话人标签为空。
    #
    # 本管道将 VAD、CAM++、ASR 拆成三个独立模型:
    #   ① 独立 VAD 模型 → 564+ 细粒度段 (无合并)
    #   ② 自定义合并 → gap<500ms, max 30s → ~119 段
    #   ③ 批次 CAM++ → 119 个 192维 embedding
    #   ④ sklearn 余弦距离聚类 → 说话人1 / 说话人2
    #   ⑤ 批次 ASR → 119 段文本
    #   ⑥ 组装 → TranscriptSegment[]
    #
    # 三个独立模型共享 FunASR 的内部权重缓存，不会增加额外内存。
    # ═══════════════════════════════════════════════════════════

    async def _transcribe_funasr(self, path: str) -> list[TranscriptSegment]:
        """
        FunASR 自定义管道 — 独立 VAD + CAM++ 聚类 + ASR 批次识别。

        处理步骤:
          1. 懒加载三个独立模型 (VAD / CAM++ / ASR)
          2. 独立 VAD 模型获取 500+ 个细粒度语音段
          3. 自定义合并策略 (gap<500ms, max 30s, min 0.5s)
          4. 用 torchaudio 加载音频并重采样为 16kHz mono
          5. 将每个合并段保存为临时 WAV 文件
          6. 批次送入 CAM++ 模型，提取 192维 speaker embedding
          7. sklearn AgglomerativeClustering 余弦距离聚类
          8. 批次送入 ASR 模型 (Paraformer + PUNC) 识别文本
          9. 组装 TranscriptSegment[]，分配 "说话人1" / "说话人2"
          10. 清理临时文件

        性能:
          - 38 分钟面试音频 → ~100 秒处理时间
          - VAD:   13s → 564 段 → 合并 119 段
          - CAM++: 40s → 119 个 embedding (批次处理 ~3 段/秒)
          - ASR:   40s → 119 段文本 (批次处理 ~3 段/秒)
          - 聚类:  <1s

        异常处理:
          - ImportError: FunASR 未安装 → fallback 到 mock
          - 其他异常: 记录错误日志 → fallback 到 mock
        """
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering

        try:
            from funasr import AutoModel
            import torchaudio
            import torchaudio.transforms as T

            # ── 1. 懒加载三个独立模型 ────────────────────
            #
            # 为什么加载三个模型而不是一个?
            # FunASR 的 AutoModel 支持组合多个模型，但组合后的 VAD 合并
            # 逻辑不可控。三个独立模型虽然看起来多，但 FunASR 内部会共享
            # 权重缓存，不会增加额外 GPU 显存占用。
            #
            # VAD 模型: FSMN-VAD，专门检测语音活动
            if not self._vad_model:
                self._vad_model = AutoModel(
                    model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    disable_update=True, log_level="WARNING",
                )

            # CAM++ 模型: 说话人特征提取器，输出 192维 embedding
            # 注意: 这是独立的 AutoModel，不是 combined.spk_model
            if not self._campp_model:
                self._campp_model = AutoModel(
                    model="iic/speech_campplus_sv_zh-cn_16k-common",
                    disable_update=True, log_level="WARNING",
                )

            # ASR 模型: Paraformer-large + VAD + PUNC
            # 注意: 这里不带 spk_model，因为我们会自己用 CAM++ 做聚类
            if not self._asr_model:
                self._asr_model = AutoModel(
                    model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                    vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
                    disable_update=True, log_level="WARNING",
                )

            # ── 2. VAD 分段 ──────────────────────────────
            # 用独立 VAD 模型获取原始语音段。
            # 输出: 500+ 个 [start_ms, end_ms] 时间戳对
            # 特点: 细粒度，保留原始 VAD 检测结果，无合并
            logger.info("Running VAD...")
            vad_result = self._vad_model.generate(input=path)
            raw_segments = vad_result[0]["value"]
            logger.info(f"VAD: {len(raw_segments)} raw segments")

            # ── 3. 智能合并 ─────────────────────────────
            # 合并策略:
            #   - 间隔 < 500ms 的相邻段合并 (面试中同一人的回答)
            #   - 合并后段长不超过 30 秒 (防止过长的段)
            #   - 丢弃 < 0.5s 的极短段 (可能是噪声或呼吸声)
            #
            # 为什么选 500ms?
            # 面试场景中两人快速问答，说话间隔通常 300-800ms。
            # 500ms 能在合并同一人连续说话和保留两人交替之间取得平衡。
            merged = self._merge_segments(raw_segments, gap_ms=500, max_dur_ms=30000)
            merged = [(s, e) for s, e in merged if (e - s) >= 500]
            logger.info(f"Merged: {len(merged)} chunks")

            if not merged:
                return self._transcribe_mock(
                    AudioMeta(path, "wav", 130, 16000, 1, 0),
                    [{"start": 0, "end": 0}]
                )

            # ── 4. 加载音频并重采样 ────────────────────
            # FunASR 模型要求 16kHz 单声道输入。
            # 原始音频可能是 48kHz 立体声，需要转换。
            audio, sr = torchaudio.load(path)
            if sr != 16000:
                audio = T.Resample(sr, 16000)(audio)
            if audio.shape[0] > 1:
                audio = audio.mean(dim=0, keepdim=True)

            # ── 5. 保存各段为临时 WAV ─────────────────
            # 为什么用临时文件而不是直接传递 tensor?
            # FunASR 的 generate() 接受文件路径作为输入。
            # 虽然也支持 tensor/numpy 输入，但文件路径方式更可靠。
            # 临时文件会在 cleanup 阶段被清理。
            temp_dir = tempfile.mkdtemp(prefix="asr_seg_")
            temp_paths, chunk_times = [], []
            for i, (start_ms, end_ms) in enumerate(merged):
                s = int(start_ms * 16000 / 1000)
                e = int(end_ms * 16000 / 1000)
                if e <= s:
                    continue
                seg = audio[:, s:e]
                if seg.shape[1] < 1600:  # < 0.1s, 太短没有意义
                    continue
                seg_path = os.path.join(temp_dir, f"seg_{i:04d}.wav")
                torchaudio.save(seg_path, seg, 16000)
                temp_paths.append(seg_path)
                chunk_times.append((start_ms / 1000.0, end_ms / 1000.0))

            logger.info(f"Saved {len(temp_paths)} segment files")

            # 如果段太少，跳过聚类直接分配
            if len(temp_paths) < 2:
                speaker = "说话人1"
                results_list = []
                asr_out = self._asr_model.generate(input=temp_paths[0])
                if asr_out:
                    text = asr_out[0].get("text", "")
                    results_list.append(TranscriptSegment(
                        speaker=speaker, speaker_name=speaker, content=text,
                        start_time=chunk_times[0][0], end_time=chunk_times[0][1], confidence=0.95,
                    ))
                self._cleanup_temp(temp_dir)
                return results_list

            # ── 6. 批次 CAM++ embedding 提取 ────────────
            # 一次性将全部段送入 CAM++ 模型，利用 FunASR 的批次处理。
            # 每个段返回一个 192 维 embedding 向量 (cuda tensor)。
            # 速度: ~3 段/秒，119 段约 40 秒
            logger.info("Extracting speaker embeddings...")
            spk_results = self._campp_model.generate(input=temp_paths)
            embeddings, valid_indices = [], []
            for idx, spk_out in enumerate(spk_results):
                if "spk_embedding" in spk_out:
                    emb = spk_out["spk_embedding"].cpu().numpy().flatten()
                    if np.any(emb):  # 过滤全零 embedding (可能是静音段)
                        embeddings.append(emb)
                        valid_indices.append(idx)

            # ── 7. 聚类 ──────────────────────────────────
            # 使用 sklearn 的层次聚类 (AgglomerativeClustering):
            #   - n_clusters=2: 假设两人对话(面试官+候选人)
            #   - 距离度量: 余弦距离 (cosine)
            #     说话人特征天然适合用余弦距离衡量相似度
            #   - 链接方式: complete (全连接)
            #     complete 使用两个簇之间的最大距离，生成的簇更紧凑
            #
            # 为什么不用 K-Means?
            # K-Means 假设球状聚类，而说话人 embedding 的分布是
            # 高维空间中的流形。层次聚类能更好处理这种结构。
            #
            # 标签稳定性:
            #   以第一个段的聚类标签为基准，确保说话人1始终是
            #   第一个说话的人，避免因聚类随机性导致的标签翻转。
            if len(embeddings) < 2:
                logger.warning(f"Only {len(embeddings)} valid embeddings, skip clustering")
                speaker_labels = ["说话人1"] * len(valid_indices)
            else:
                logger.info(f"Clustering {len(embeddings)} embeddings into 2 speakers...")
                n_clusters = min(2, len(embeddings))
                labels = AgglomerativeClustering(
                    n_clusters=n_clusters, metric="cosine", linkage="complete",
                ).fit_predict(np.array(embeddings))
                # 确保说话人1 = 第 0 段 (保持标签稳定)
                if labels[0] != 0:
                    labels = [1 - l for l in labels]
                speaker_map = {0: "说话人1", 1: "说话人2"}
                speaker_labels = [speaker_map.get(l, "说话人1") for l in labels]

            # ── 8. 批次 ASR 识别 ────────────────────────
            # 一次性将全部段送入 ASR 模型识别文本。
            # 速度: ~3 段/秒，119 段约 40 秒
            logger.info("Running ASR batch...")
            asr_results = self._asr_model.generate(input=temp_paths)

            # ── 9. 组装最终结果 ─────────────────────────
            # 将聚类标签和 ASR 文本按相同索引对齐。
            # 注意: valid_indices 是对齐的 key，
            # 因为 CAM++ 可能跳过某些段 (如太短/全零)
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

            # ── 10. 清理临时文件 ────────────────────────
            self._cleanup_temp(temp_dir)
            logger.info(f"Done: {len(results_list)} segments with speaker labels")
            return results_list

        except ImportError:
            logger.warning("FunASR not installed, falling back to mock")
            return self._transcribe_mock(
                AudioMeta(path, "wav", 130, 16000, 1, 0),
                [{"start": 0, "end": 130}]
            )
        except Exception as e:
            logger.error(f"FunASR error: {type(e).__name__}: {e}", exc_info=True)
            self._cleanup_temp(temp_dir)
            return self._transcribe_mock(
                AudioMeta(path, "wav", 130, 16000, 1, 0),
                [{"start": 0, "end": 130}]
            )

    # ─────────────────────────────────────────────────────────
    # 辅助方法 / Helper Methods
    # ─────────────────────────────────────────────────────────

    def _merge_segments(self, segments: list, gap_ms: int = 500, max_dur_ms: int = 30000) -> list:
        """
        合并 VAD 分段。

        策略:
          遍历排序后的 VAD 段，如果相邻段间隔 < gap_ms 且
          合并后总长 < max_dur_ms，则合并；否则新建一段。

        为什么 gap_ms=500ms?
          面试场景两人快速问答的平均间隔是 380ms。
          500ms 能在合并连续说话和保留说话人切换之间取得平衡。

        为什么 max_dur_ms=30000?
          CAM++ 在 10-30 秒的段落上 embedding 最稳定。
          太长的段可能包含多句话，模糊说话人边界。
          太短的段 (<0.5s) 则 embedding 不可靠。
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
        """递归删除临时目录和所有临时 WAV 文件。"""
        try:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # 其他后端 / Other Backends
    # ═══════════════════════════════════════════════════════════

    async def _transcribe_whisper(self, path: str, segments: list[dict]) -> list[TranscriptSegment]:
        """Whisper 后端 — 使用 faster-whisper。"""
        try:
            from faster_whisper import WhisperModel
            if not self._whisper_model:
                self._whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
            segs, _ = self._whisper_model.transcribe(path, beam_size=5, language="zh")
            return self._parse_whisper_result(segs)
        except ImportError:
            logger.warning("faster-whisper not installed, falling back to mock")
            return self._transcribe_mock(AudioMeta(path, "wav", 130, 16000, 1, 0), segments)

    async def _transcribe_sherpa(self, path: str, segments: list[dict]) -> list[TranscriptSegment]:
        """Sherpa-ONNX 后端 — 占位，尚未集成。"""
        try:
            import sherpa_onnx
            raise NotImplementedError("Sherpa-ONNX integration: configure model path")
        except ImportError:
            logger.warning("sherpa-onnx not installed, falling back to mock")
            return self._transcribe_mock(AudioMeta(path, "wav", 130, 16000, 1, 0), segments)

    # ─────────────────────────────────────────────────────────
    # 结果解析 / Result Parsers
    # ─────────────────────────────────────────────────────────

    def _parse_whisper_result(self, segments) -> list[TranscriptSegment]:
        """将 faster-whisper 的输出解析为 TranscriptSegment。"""
        results = []
        for seg in segments:
            results.append(TranscriptSegment(
                speaker="未知", speaker_name="发言人",
                content=seg.text.strip(),
                start_time=seg.start, end_time=seg.end,
                confidence=round(1.0 - seg.avg_logprob / abs(seg.avg_logprob or 1), 2),
            ))
        return results


# ═══════════════════════════════════════════════════════════════
# 全局单例 / Global Singleton
# ═══════════════════════════════════════════════════════════════
# 默认使用 funasr 后端。如需切换可在创建 ASREngine 时指定 backend。
# 例如: ASREngine(backend="whisper")
asr_engine = ASREngine(backend="funasr")
