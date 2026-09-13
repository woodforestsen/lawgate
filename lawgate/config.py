# -*- coding: utf-8 -*-
"""全局配置与路径解析。

设计原则：
1. 所有路径相对仓库根，可被 configs/base.yaml 覆盖；
2. 模型/分词器等重资源统一走 ``resolve_*``，本地目录优先、HF repo id 兜底，
   从而在离线（HF_HUB_OFFLINE=1）与在线两种环境下都能工作；
3. 记录"实际生效后端"，供文档与结果图注自动写明硬件与模型来源。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        # 无 PyYAML 时的极简兜底：只解析 key: value 顶层标量
        out: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line or line.startswith("-"):
                continue
            k, v = line.split(":", 1)
            v = v.strip().strip("'\"")
            if v in ("true", "false"):
                out[k.strip()] = v == "true"
            else:
                try:
                    out[k.strip()] = int(v)
                except ValueError:
                    out[k.strip()] = v
        return out


# ---------------------------------------------------------------- 模型候选
# 顺序即优先级：本地目录 → HF 缓存 repo id。
# fuzi-mingcha-v1_0（夫子·明察，ChatGLM-6B 底座的司法大模型）优先，
# 仓库自带 modeling_chatglm.py，需 trust_remote_code=True（HFLLM 已默认传入）。
# 0.5B 已在本机 HF 缓存中；1.5B 由 scripts/fetch_hf_files.py 拉到 models/。
CAUSAL_MODEL_CANDIDATES = [
    "models/fuzi-mingcha-v1_0",
    "models/qwen2.5-1.5b-instruct",
    "models/qwen2.5-0.5b-instruct",
    "Qwen/Qwen2.5-1.5B-Instruct",
    "Qwen/Qwen2.5-0.5B-Instruct",
]

EMBED_MODEL_CANDIDATES = [
    "models/bge-small-zh-v1.5",
    "BAAI/bge-small-zh-v1.5",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
]

# 草稿（门控）模型候选：**只用来取前 k 个 token 的 logprob 分布**，不生成答案。
# 为什么回答模型换成 API 之后本地还需要一个小模型：见 docs/deviations.md D30——
# 门控信号 u 是"模型自己有多不确定"，需要 token 级分布；API 只在**思考模式**下
# 返回真实分布（关思考时返回退化分布，全是 0.0 / -9999），所以保留一条本地兜底。
DRAFT_MODEL_CANDIDATES = [
    "models/qwen2.5-0.5b-instruct",
    "models/qwen2.5-1.5b-instruct",
]

# 已在本机 HF 缓存中的确定性兜底（无网络、无下载时仍可运行）：
HF_CACHE_SNAPSHOT_GLOBS = [
    "E:/ModelCache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/*",
    str(Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/*"),
]


_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin",
                 "model.safetensors.index.json", "pytorch_model.bin.index.json")


def _has_weights(p: Path) -> bool:
    """目录必须同时有 config.json 与**实际权重**才算可用。

    否则会把"刚下完 config.json、权重还在下载"的半成品目录当成可用模型，
    后续 from_pretrained 直接报错（曾因此把 models/qwen2.5-1.5b-instruct 误判可用）。
    """
    if not (p / "config.json").exists():
        return False
    return any((p / w).exists() for w in _WEIGHT_FILES)


def _first_existing_dir(paths) -> str | None:
    for p in paths:
        pp = Path(p)
        if pp.is_dir() and _has_weights(pp):
            return str(pp)
    return None


def _snapshot_dirs(pattern: str) -> list[str]:
    import glob as _glob

    return sorted([d for d in _glob.glob(pattern) if _has_weights(Path(d))],
                  reverse=True)


def _as_bool(raw) -> bool:
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _pretty_model_name(path: str) -> str:
    """把各种"模型路径"变成一句人话标签（图注/页脚/health 都用它）。

    三种来源都要能看懂：
      * 仓库内目录          models/bge-small-zh-v1.5        → bge-small-zh-v1.5
      * HF 仓库缓存快照      ...\\models--Qwen--Qwen2.5-0.5B-Instruct\\snapshots\\<哈希>
                            → Qwen/Qwen2.5-0.5B-Instruct（**不是**那串哈希）
      * HF repo id（未落地）  BAAI/bge-small-zh-v1.5          → 原样
    """
    p = Path(path or "")
    if p.is_dir():
        for part in p.parts:
            if part.startswith("models--"):
                return part[len("models--"):].replace("--", "/")
        return p.name
    return str(path or "")


def _resolve_provider(llm_backend: str, api_key: str) -> str:
    """``llm_backend`` → 服务形态 local / deepseek。

    * ``deepseek``：显式要求走 API（**没有密钥也照样走**——让报错发生在调用处，
      写明"401 缺少密钥"，而不是悄悄换成本地模型跑出另一套结果）；
    * ``hf`` / ``vllm`` / ``rule``：显式要求本地；
    * ``auto``：有密钥就走 API，没密钥就本地。
    """
    b = (llm_backend or "auto").strip().lower()
    if b == "deepseek":
        return "deepseek"
    if b in ("hf", "vllm", "rule", "local"):
        return "local"
    return "deepseek" if api_key else "local"


@dataclass
class Settings:
    """运行期配置。"""

    root: Path = REPO_ROOT
    # 数据与产物
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    raw_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "raw")
    judgments_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "judgments")
    kb_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "kb")
    bench_dir: Path = field(default_factory=lambda: REPO_ROOT / "data" / "benchmark")
    results_dir: Path = field(default_factory=lambda: REPO_ROOT / "results")
    figures_dir: Path = field(default_factory=lambda: REPO_ROOT / "figures")
    docs_dir: Path = field(default_factory=lambda: REPO_ROOT / "docs")
    configs_dir: Path = field(default_factory=lambda: REPO_ROOT / "configs")

    # 库文件
    db_path: str = "data/kb/legal_facts.db"
    chroma_path: str = "data/kb/chroma"

    # 模型
    causal_model: str = ""
    embed_model: str = ""
    llm_backend: str = "auto"  # auto | deepseek | hf | vllm | rule
    device: str = "cpu"
    load_in_4bit: bool = False
    # 权重精度：auto（有 CUDA 用 fp16 / 纯 CPU 用 fp32）| float16 | float32 | bfloat16
    # 注意：像 fuzi-mingcha（ChatGLM-6B 底座，6.7B 参数）这种大模型在 CPU 上必须
    # 显式 float16，否则 fp32 要 ~27 GB 内存（见 docs/deviations.md D-fuzi-5）。
    dtype: str = "auto"

    # ------------------------------------------------ 最终回答模型的服务形态（D30）
    # local  = 本机权重（transformers/vLLM，见 causal_model）
    # deepseek = DeepSeek 官方 API（OpenAI 兼容 /chat/completions）
    # 由 llm_backend 推导，不单独配置，避免"两处说法不一致"。
    llm_provider: str = "local"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""
    # 思考模式：False（默认，关思考）→ 延迟 1–3 s、答案直接产出；
    # True → 先生成一大段 reasoning（可能把 max_tokens 全吃掉、content 为空）。
    deepseek_thinking: bool = False
    deepseek_timeout: float = 120.0
    deepseek_max_retries: int = 3

    # 门控草稿来源（u 的信号从哪来）：local | api | none | auto
    #   local ：本机小模型（draft_model）取 logprobs（默认；实测 u 有区分度）
    #   api   ：用回答模型自己的 logprobs（仅思考模式返回真实分布；实测 u 塌缩到 ≈0）
    #   none  ：不加载任何草稿模型，只留确定性复杂度评分
    #   auto  ：按 local → api → rule 依次尝试，**每次实际用了哪条写进 trace**
    draft_source: str = "auto"
    draft_model: str = ""
    draft_model_source: str = "未解析"

    # 门控
    k_draft: int = 20
    signal: str = "margin"
    tau_default: float = 0.10

    # 生成
    max_new_tokens: int = 192
    n_workers: int = 1

    # 评测
    seeds: tuple[int, ...] = (0, 1, 2)
    allow_network: bool = False

    # 溯源
    hardware_note: str = ""
    run_date: str = ""
    # 因果模型路径是怎么定下来的（显式配置 / 环境变量 / 自动挑选 / 缓存兜底）
    model_source: str = "未解析"

    def __post_init__(self) -> None:
        y = _load_yaml(self.root / "configs" / "base.yaml")
        for k, v in y.items():
            if hasattr(self, k) and v is not None:
                setattr(self, k, v)
        # dtype 也可由环境变量覆盖（离线测试/对照实验常用）
        self.dtype = os.environ.get("LAWGATE_DTYPE") or self.dtype

        # -------------------------------------------------- DeepSeek API（D30）
        # 密钥只从环境变量/.env 读（绝不写进 base.yaml，那会被提交）；
        # 单独用 _env 取值是为了"空字符串也能覆盖"这种场景不留坑。
        self.deepseek_model = (os.environ.get("DEEPSEEK_MODEL")
                               or self.deepseek_model or "deepseek-v4-flash")
        self.deepseek_base_url = (os.environ.get("DEEPSEEK_BASE_URL")
                                  or self.deepseek_base_url
                                  or "https://api.deepseek.com")
        self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY") or self.deepseek_api_key
        for env_key, attr, cast in (("LAWGATE_LLM_THINKING", "deepseek_thinking", _as_bool),
                                    ("LAWGATE_LLM_TIMEOUT", "deepseek_timeout", float),
                                    ("LAWGATE_LLM_RETRIES", "deepseek_max_retries", int)):
            raw = os.environ.get(env_key)
            if raw not in (None, ""):
                try:
                    setattr(self, attr, cast(raw))
                except (TypeError, ValueError):
                    pass
        # llm_backend 决定服务形态；也可被 LAWGATE_LLM_PROVIDER 直接钉死
        self.llm_backend = os.environ.get("LAWGATE_LLM_PROVIDER") or self.llm_backend
        self.llm_provider = _resolve_provider(self.llm_backend, self.deepseek_api_key)

        self.draft_source = (os.environ.get("LAWGATE_DRAFT_SOURCE")
                             or self.draft_source or "auto").lower()
        env_draft = os.environ.get("LAWGATE_DRAFT_MODEL")
        if env_draft is None:
            env_draft = self.draft_model
        if str(env_draft).strip().lower() in ("none", "off", "0"):
            self.draft_model = ""
            self.draft_model_source = "显式关闭（LAWGATE_DRAFT_MODEL=none）"
        elif str(env_draft).strip():
            self.draft_model = str(env_draft).strip()
            self.draft_model_source = "环境变量 LAWGATE_DRAFT_MODEL / base.yaml"
        else:
            auto_draft = _first_existing_dir(DRAFT_MODEL_CANDIDATES)
            snap_draft = (_snapshot_dirs(HF_CACHE_SNAPSHOT_GLOBS[0])
                          or _snapshot_dirs(HF_CACHE_SNAPSHOT_GLOBS[1]) or [""])[0]
            self.draft_model = auto_draft or snap_draft or ""
            self.draft_model_source = (
                "候选目录自动挑选（config.py：DRAFT_MODEL_CANDIDATES）" if auto_draft else
                "HF 缓存快照兜底" if snap_draft else
                "未找到本地草稿模型（门控将只用确定性复杂度评分）")

        if not self.causal_model:
            env_m = os.environ.get("LAWGATE_MODEL")
            auto = _first_existing_dir(CAUSAL_MODEL_CANDIDATES)
            snap = (_snapshot_dirs(HF_CACHE_SNAPSHOT_GLOBS[0])
                    or _snapshot_dirs(HF_CACHE_SNAPSHOT_GLOBS[1]) or [""])[0]
            self.causal_model = env_m or auto or snap or CAUSAL_MODEL_CANDIDATES[-1]
            self.model_source = (
                "环境变量 LAWGATE_MODEL" if env_m else
                "候选目录自动挑选（config.py：CAUSAL_MODEL_CANDIDATES）" if auto else
                "HF 缓存快照兜底" if snap else "无条件可用模型（最后兜底）")
        else:
            self.model_source = "configs/base.yaml 的 causal_model（显式指定）"
        # 服务形态优先：走 API 时"最终回答模型"根本不是本地权重，
        # 图注/页脚/health 必须说清是**哪个 API 上的哪个模型**（D30）。
        if self.llm_provider == "deepseek":
            self.model_source = (
                f"DeepSeek API（{self.deepseek_base_url}，模型 {self.deepseek_model}）"
                + ("" if self.deepseek_api_key else "；⚠ 未读到 API Key（DEEPSEEK_API_KEY）"))
        if not self.embed_model:
            self.embed_model = (
                os.environ.get("LAWGATE_EMBED")
                or _first_existing_dir(EMBED_MODEL_CANDIDATES)
                or EMBED_MODEL_CANDIDATES[-1]
            )
        if not self.hardware_note:
            import platform

            self.hardware_note = f"CPU {platform.machine()} / {os.cpu_count()} cores / no CUDA"
        if not self.run_date:
            import datetime

            self.run_date = datetime.date.today().isoformat()

    # ------------------------------------------------------------ 模型信息
    def model_is_local(self, which: str = "causal") -> bool:
        if which == "causal" and self.llm_provider == "deepseek":
            return False          # 走 API 时没有本地权重
        if which == "draft":
            return bool(self.draft_model) and Path(self.draft_model).is_dir()
        p = self.causal_model if which == "causal" else self.embed_model
        return Path(p).is_dir()

    def model_label(self, which: str = "causal") -> str:
        """用于图注的简短模型名。"""
        if which == "causal" and self.llm_provider == "deepseek":
            return self.deepseek_model
        if which == "draft":
            if not self.draft_model:
                return "（无本地草稿模型）"
            return _pretty_model_name(self.draft_model)
        p = self.causal_model if which == "causal" else self.embed_model
        return _pretty_model_name(p)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.raw_dir, self.judgments_dir, self.kb_dir,
                  self.bench_dir, self.results_dir, self.figures_dir,
                  self.docs_dir, self.configs_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

    def provenance(self) -> dict:
        """写入每份结果文件的运行环境指纹（手册要求所有图有硬件/模型/日期图注）。"""
        return {
            "hardware": self.hardware_note,
            "device": self.device,
            "llm_provider": self.llm_provider,          # local | deepseek
            "llm_api_base": (self.deepseek_base_url
                             if self.llm_provider == "deepseek" else None),
            "llm_api_model": (self.deepseek_model
                              if self.llm_provider == "deepseek" else None),
            "llm_thinking": (self.deepseek_thinking
                             if self.llm_provider == "deepseek" else None),
            "has_api_key": bool(self.deepseek_api_key),
            "draft_source": self.draft_source,          # auto | api | local | none
            "draft_model": self.model_label("draft"),
            "draft_model_path": self.draft_model,
            "draft_model_source": self.draft_model_source,
            "causal_model": self.model_label("causal"),
            "causal_model_path": self.causal_model,
            "causal_model_dtype": self.dtype,
            "causal_model_source": self.model_source,
            "embed_model": self.model_label("embed"),
            "llm_backend": self.llm_backend,
            "date": self.run_date,
            "version": __import__("lawgate").__version__,
        }

    def dump(self) -> str:
        return json.dumps(self.provenance(), ensure_ascii=False, indent=2)


_SETTINGS: Settings | None = None


def get_settings(**overrides) -> Settings:
    global _SETTINGS
    if _SETTINGS is None or overrides:
        _SETTINGS = Settings(**overrides)
    return _SETTINGS
