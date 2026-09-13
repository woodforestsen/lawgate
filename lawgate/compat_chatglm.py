# -*- coding: utf-8 -*-
"""旧版远程代码模型（ChatGLM 系）在 Windows + 新版 transformers 上的兼容装载层。

为什么需要这一层
----------------
本仓库位于 ``D:\\桌面\\lawgate``（含中文）。经实测，直接
``AutoTokenizer.from_pretrained("models/fuzi-mingcha-v1_0", trust_remote_code=True)``
会在本机连续撞上三堵墙，且**三堵都不是模型的问题**：

1. **sentencepiece 读不了中文路径**（D-fuzi-1）
   ``ice_text.model`` 必须由 sentencepiece 加载；而 sentencepiece 把路径交给 C++
   后按 ANSI(936) 解码，中文路径必然报
   ``OSError: Not found: "D:\\????\\lawgate\\...\\ice_text.model"``；
   同一个文件改用 ``LoadFromSerializedProto(bytes)``（自己读字节）则加载正常
   （实测 vocab_size=130344）。
   → ``_patch_sentencepiece_loader()``：把 ``Load/LoadFromFile`` 换成"先读字节"。

2. **旧 tokenizer 的 ``_pad`` 签名与新 transformers 不兼容**（D-fuzi-2）
   ChatGLM-6B 系 tokenizer 只有 ``_pad(encoded_inputs, max_length,
   padding_strategy, pad_to_multiple_of, return_attention_mask)``，
   而新版 ``PreTrainedTokenizerBase.pad()`` 会多传一个 ``padding_side`` →
   ``TypeError: ChatGLMTokenizer._pad() got an unexpected keyword argument
   'padding_side'``（甚至连 ``tok.encode()`` 都过不去）。
   → ``_newest_compat_tokenizer()``：补一个吃掉多余关键字参数的 ``_pad``。
   这里**只补签名，不改行为**（``padding_side`` 对本演示无意义：我们每次只喂一条
   提问，从不 batch padding）。

3. **ChatGLM 必须自己拼 [gMASK]<sop>**（D-fuzi-3）
   ChatGLM 的生成入口要求输入形如 ``[gMASK] <sop> 问：... 答：``，
   缺了这两个特殊 token 会输出乱码/不终止。
   → ``CFG`` 里的 ``gmask``/``sop`` 前缀由 ``ChatGLMTokenizerAdapter`` 负责补。

设计原则
--------
* **不改模型目录里的任何文件**（那是从 ModelScope 下的原始权重，改了就无法与
  官方校验对齐）；所有适配都发生在内存里（monkeypatch）。
* **不改依赖版本**：本机 transformers 是给 Qwen 等其它模型共用的，降级会连带
  影响仓库其它部分。因此选择"适配旧代码"而不是"降级框架"。
* 所有兼容动作都是**幂等**的：重复调用不会叠加补丁。

用法
----
    from lawgate.compat_chatglm import load_chatglm
    bundle = load_chatglm("models/fuzi-mingcha-v1_0", dtype="float16")
    bundle.tokenizer / bundle.model / bundle.name
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from pathlib import Path

# 必须在 import transformers 之前生效：见 lawgate/env_setup.py 的 HF_MODULES_CACHE 说明
from lawgate.env_setup import apply as _apply_env

_apply_env()

_SP_PATCHED = False
_PAD_PATCHED: set = set()
_TIED_PATCHED: set = set()
_GEN_PATCHED: dict = {}

CFG = {
    "gmask": "[gMASK]",
    "sop": "<sop>",
}


# --------------------------------------------------------------- 1) sentencepiece
def _patch_sentencepiece_loader() -> bool:
    """让 sentencepiece 用"读字节"的方式加载词表，绕开 Windows 中文路径问题。

    返回是否本次真的做了补丁（幂等）。
    """
    global _SP_PATCHED
    if _SP_PATCHED:
        return False
    try:
        import sentencepiece as spm
    except Exception:  # noqa: BLE001
        return False

    raw_load = spm.SentencePieceProcessor.Load

    def _load(self, model_file, *args, **kwargs):  # noqa: ANN001
        try:
            p = Path(model_file)
            if p.is_file():
                self.LoadFromSerializedProto(p.read_bytes())
                return True
        except OSError:
            pass          # 读不到就退回原实现，让它抛原本的错
        return raw_load(self, model_file, *args, **kwargs)

    spm.SentencePieceProcessor.Load = _load            # type: ignore[method-assign]
    spm.SentencePieceProcessor.LoadFromFile = _load    # type: ignore[method-assign]
    _SP_PATCHED = True
    return True


# ------------------------------------------------- 3) 缺 all_tied_weights_keys
def _patch_generation_mixin(model_cls: type) -> type:
    """把 ``generate`` 系列能力装回旧模型类（transformers 5.x 剥离了 GenerationMixin）。

    transformers 4.50 起 ``PreTrainedModel`` 不再继承 ``GenerationMixin``，而老模型代码
    （ChatGLM-6B 系）只写了 ``prepare_inputs_for_generation``，没有在自己的基类里混入
    GenerationMixin。结果：权重加载一切正常，但一调 ``model.generate(...)`` 就
    ``AttributeError: 'ChatGLMForConditionalGeneration' object has no attribute
    'generate'``（D-fuzi-8）。

    这里动态派生一个子类补上混入（不改模型目录里的源文件）：
        class _ChatGLMWithGeneration(ChatGLMForConditionalGeneration, GenerationMixin)
    顺序很关键：**GenerationMixin 必须在后面**，否则它会覆盖模型自己的
    ``prepare_inputs_for_generation`` / ``_reorder_cache`` / ``_update_model_kwargs_*``，
    这些正是 ChatGLM 的特殊之处（gMASK 位置编码、[gMASK] 掩码）。

    本仓库**必须**能拿到 ``generate``（而不是只用官方 ``.chat()``）的原因有二：
      1. 门控草稿（``draft_logprobs``）要 ``output_scores=True`` 的逐位 top-k 分布；
      2. 流式输出要 ``TextIteratorStreamer``，且要与整段生成**同键同参**（贪心）——
         ``.chat()`` 默认 do_sample=True，会让缓存键失效、演示不可复现。
    """
    name = f"{model_cls.__module__}.{model_cls.__name__}"
    if name in _GEN_PATCHED:
        return _GEN_PATCHED[name]
    # 已经有生成能力（老版 transformers 的 PreTrainedModel 自带，或模型自带）→ 不动
    if hasattr(model_cls, "generate"):
        _GEN_PATCHED[name] = model_cls
        return model_cls
    try:
        from transformers import GenerationMixin
    except Exception:  # noqa: BLE001
        _GEN_PATCHED[name] = model_cls
        return model_cls
    sub = type(model_cls.__name__, (model_cls, GenerationMixin),
               {"__doc__": model_cls.__doc__})
    _GEN_PATCHED[name] = sub
    return sub


# ------------------------------------------------- 4) 缺 all_tied_weights_keys（属性）
def _patch_tied_weights_keys(model_cls: type) -> bool:
    """给旧模型类补 ``all_tied_weights_keys``（新版 transformers 的加载收尾要用）。

    transformers 5.x 的 ``_finalize_model_loading()`` 里有：
        ``for key in missing_keys - self.all_tied_weights_keys.keys()``
    旧版 ``PreTrainedModel`` 根本没有这个属性，于是权重都读完了却在收尾时抛
    ``AttributeError: 'ChatGLMForConditionalGeneration' object has no attribute
    'all_tied_weights_keys'``（D-fuzi-7）。

    填**空集合**是准确的而不是凑数：本模型 368 个权重全部来自 checkpoint
    （没有任何 tied 缺失），且 lm_head 与词嵌入是两张独立的权重（分别落在第 15 与
    第 2 个分片），本来就不存在"共享/绑定"的权重。
    """
    name = f"{model_cls.__module__}.{model_cls.__name__}"
    if name in _TIED_PATCHED:
        return False
    if "all_tied_weights_keys" not in model_cls.__dict__:
        # 必须是 **dict**：transformers 5.x 里对它调用 .keys()（不是集合语义）
        model_cls.all_tied_weights_keys = {}           # type: ignore[attr-defined]
        if not hasattr(model_cls, "_tied_weights_keys") or \
                model_cls._tied_weights_keys is None:
            model_cls._tied_weights_keys = []         # type: ignore[attr-defined]
    _TIED_PATCHED.add(name)
    return True


# ------------------------------------------- 2) 旧 config 的属性别名（新版框架要看）
_CONFIG_ALIASES = {
    # 新版 GenerationMixin 预分配 KV cache 时读 num_hidden_layers；
    # ChatGLM 的 config 里叫 num_layers（D-fuzi-9）
    "num_hidden_layers": "num_layers",
    "n_layer": "num_layers",
}


def _alias_config_attrs(cfg) -> list[str]:
    """按需给旧 config 补新版框架要读的属性名（值取自模型自己的字段）。"""
    added: list[str] = []
    for new, old in _CONFIG_ALIASES.items():
        if getattr(cfg, new, None) is None:
            val = getattr(cfg, old, None)
            if val is not None:
                try:
                    setattr(cfg, new, val)
                    added.append(f"{new}={val}")
                except Exception:  # noqa: BLE001
                    pass
    return added


# ------------------------------------------------- 5) 新旧 KV cache 桥接
class ChatGLMCacheBridge(__import__("torch").nn.Module):
    """把"新版框架的 ``DynamicCache``"与"旧 ChatGLM 的元组缓存"桥接起来（D-fuzi-10）。

    背景（实测链条）
    ----------------
    transformers 5.x 的 ``generate`` 会先造一个 ``DynamicCache`` 塞进
    ``model_kwargs["past_key_values"]``，而 ChatGLM-6B 系 ``forward`` 里写的是
        ``layer_past = past_key_values[i]``   # 期望 元组/列表
    → ``TypeError: 'DynamicCache' object is not subscriptable``。
    就算把 DynamicCache 塞成可下标对象，它仍然会在每层调用
    ``past_key_values.update(key, value, layer_idx)`` 时**再拼一次**，而旧代码
    传进来的已经是"拼好的整段"，会重复拼接造成错误上下文（实测推理量也翻倍）。

    做法
    ----
    **不参与框架的缓存管理，自己拿住旧式的 ``presents``**：
      1. 首次（prefill）仍把 ``past_key_values`` 传 ``None``，让模型按官方路径生成
         4D 掩码 + 2D 位置编码（这是 ChatGLM 正确的注意力形状）；
      2. 后续步传入上一步模型自己返回的 ``presents``（元组版），不碰 DynamicCache；
      3. 对上层 ``generate`` **始终返回 ``past_key_values=None``**（即"没有东西给框架
         记录"），框架于是每步只把新 token 的手工切片逻辑交给我们——
         由 ``prepare_inputs_for_generation`` 依据我们的 past 长度自己切。

    这样既保留 KV cache（否则 6.7B 模型在 CPU 上一步一次全序列前向，慢到不可用），
    又完全不用改模型仓库里的 ``modeling_chatglm.py``。

    正确性守望：``ChatGLMForConditionalGeneration.prepare_inputs_for_generation``
    用的是 ``if past is not None or past_key_values is not None`` ——我们的
    ``presents`` 是真值，故它会正确地把输入切成最后一个 token 并算出 2D 位置编码。
    """

    def __init__(self, model):
        super().__init__()          # nn.Module：这样 parameters()/to()/train() 等全部可用
        self.model = model
        self.past: tuple | None = None
        self.prefill_len: int = 0
        self.mask_pos: int = 0      # [gMASK] 在 prompt 中的下标（ChatGLM 2D 位置编码要用）
        self._prefilled: bool = False
        # 防"绕道"（D-fuzi-15）：内层模型自己也有 generate，谁直接调
        # ``bundle.model.model.generate(...)`` 就会绕过缓存桥接而崩。这里把内层入口
        # 改道回本壳；原实现另存，供本壳自己调用。
        inner_gen = getattr(model, "generate", None)
        if inner_gen is not None:
            self._inner_generate = inner_gen
            model.generate = lambda *a, **kw: self.generate(*a, **kw)

    # 透传给内层模型（配置、generate、chat、prepare_inputs_for_generation 等）
    def __getattr__(self, item):  # noqa: ANN001
        try:
            return super().__getattr__(item)
        except AttributeError:
            return getattr(self.model, item)

    def forward(self, *args, **kwargs):  # noqa: ANN001
        pkv = kwargs.pop("past_key_values", None)
        if pkv is not None:
            # 框架每步都会塞一个 DynamicCache 进来；我们只信自己那份 past
            kwargs["past_key_values"] = self.past
        kwargs["use_cache"] = True

        # 掩码形状（D-fuzi-13）：新版 generate 默认递过来的是 **2D int64** 注意力掩码
        # （padding 用），而这份旧模型的注意力里写的是
        #     attention_scores.masked_fill_(attention_mask, -10000.0)
        # —— 需要 **bool** 掩码；旧实现是"2D 就自己造 4D bool 因果掩码"（见
        # modeling_chatglm.py 的 get_masks）。故这里把 2D 掩码摘掉，交给模型自己造，
        # 既保住 gMASK 因果形状，也避免 dtype 崩溃。
        am = kwargs.get("attention_mask")
        if isinstance(am, (list, tuple)) or (hasattr(am, "dim") and am.dim() <= 2):
            kwargs["attention_mask"] = None

        ii = kwargs.get("input_ids")
        if ii is None and args:
            ii = args[0]
        # 预填/解码如何判定（D-fuzi-16）：**不能**用"框架有没有传 past_key_values"来判断——
        # 新版 generate 从第一步起就塞一个**空** DynamicCache 进来，据此判断会把预填步
        # 当成解码步：于是位置编码用了块序号、prompt 长度也没记下，输出退化成
        # "拖欠工资怎么办? 拖欠工资,你有权利,你有权利向劳动仲裁机构机构机构机构…"。
        # 唯一可靠的判据是"预填是否已经完成"（_prefilled）且 past 已建立。
        # 注意 _prefilled 的语义是"预填过了"，**不是**"这一层已经填过"，
        # 条件写反会把所有解码步都当成预填（D-fuzi-17）。
        decode_step = self._prefilled and self.past is not None

        if decode_step:
            # 解码步的 2D 位置编码（D-fuzi-14）：框架只递最后一个 token，
            # 而 ChatGLM 的位置编码必须是 [mask位置, 已生成块序号]。
            # 框架的 _update_model_kwargs_for_generation 在这种情况下给出的是 None，
            # 直接用会在 attention 里炸（'NoneType' object has no attribute 'max'），
            # 故按模型自己的规则补上：block = past 长度 - 预填长度 + 1。
            kwargs["position_ids"] = self._decode_position_ids(ii)
        else:
            kwargs.pop("position_ids", None)      # 预填交给模型自己算 2D 位置编码

        try:
            out = self.model(*args, **kwargs)
        except AttributeError as exc:
            if "NoneType" in str(exc) and "max" in str(exc):
                raise RuntimeError(
                    "ChatGLM 位置编码缺失：桥接层未能补出 decode 步的 position_ids"
                    "（见 compat_chatglm.ChatGLMCacheBridge）") from exc
            raise
        presents = None
        if isinstance(out, dict):
            presents = out.get("past_key_values")
        elif hasattr(out, "past_key_values"):
            presents = out.past_key_values
            try:
                out.past_key_values = None       # 不把元组交给框架
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(out, (tuple, list)) and len(out) > 1:
            presents = out[1]
        self.past = presents if presents is not None else self.past
        if not self._prefilled and self.past is not None:
            self._remember_prefill(ii)        # 预填结束后才能知道 prompt 长度
            self._prefilled = True
        return out

    # ------------------------------------------------------------ 位置编码工具
    def _remember_prefill(self, input_ids) -> None:  # noqa: ANN001
        if input_ids is None or not hasattr(input_ids, "tolist"):
            return
        try:
            seq = input_ids.tolist()[0]
            cfg = self.model.config
            mask_tok = cfg.gmask_token_id if cfg.gmask_token_id in seq else cfg.mask_token_id
            self.mask_pos = int(seq.index(mask_tok))
            self.prefill_len = int(input_ids.shape[1])
        except Exception:  # noqa: BLE001
            self.mask_pos, self.prefill_len = 0, 0

    def _decode_position_ids(self, input_ids):  # noqa: ANN001
        """ChatGLM 2D 位置编码：``[[mask位置], [块序号]]``，块序号 = 已生成长度 + 1。"""
        import torch

        past_len = 0
        if self.past:
            p0 = self.past[0]
            if p0 is not None and hasattr(p0, "shape"):
                past_len = int(p0[0].shape[0])          # [seq_len, batch, ...]
        block = past_len - self.prefill_len + 1 if self.prefill_len else past_len + 1
        dev = input_ids.device if hasattr(input_ids, "device") else "cpu"
        # 形状必须是 [batch, 2, seq_len]（模型内部按 position_ids[:, 1, :] 取块位置）
        return torch.tensor(
            [[[self.mask_pos], [max(block, 1)]]], dtype=torch.long, device=dev)

    def reset(self) -> None:
        self.past = None
        self.prefill_len = 0
        self.mask_pos = 0
        self._prefilled = False

    # ------------------------------------------------------------------ 生成入口
    def generate(self, *args, **kwargs):  # noqa: ANN001
        """在内层模型上以 **本壳** 作为 self 执行 ``GenerationMixin.generate``。

        为什么必须显式写这一层（D-fuzi-11）：``nn.Module.__getattr__`` 会把未定义的
        ``generate`` 透传给内层模型，于是 ``generate`` 里的 ``self(...)`` 指的是**内层**
        模型，整个缓存桥接（``forward``）就被绕过了 —— 结果照旧
        ``TypeError: 'DynamicCache' object is not subscriptable``。
        这里把 `generate` 绑到内层模型的实现上、但 self 仍是本壳，生成循环里每次
        ``self(**model_inputs)`` 才会落到本壳的 ``forward``。
        （模型自己的 ``prepare_inputs_for_generation`` / ``_update_model_kwargs_*``
        仍由 ``__getattr__`` 透传，ChatGLM 的特殊逻辑不受影响。）
        """
        inner_gen = getattr(self, "_inner_generate", None)
        if inner_gen is None:
            inner_gen = getattr(self.model, "generate")
        self.reset()                         # 每次 generate 都是一次新的会话
        return inner_gen.__func__(self, *args, **kwargs)  # type: ignore[attr-defined]


def wrap_chatglm(model, bridge: bool = True):  # noqa: ANN001
    """按需给 ChatGLM 模型套上缓存桥接壳。"""
    if not bridge:
        return model
    if isinstance(model, ChatGLMCacheBridge):
        model.reset()
        return model
    # 壳必须**同时**是 GenerationMixin：新版 generate 是用 ``getattr(type(self), ...)``
    # 取解码函数（_sample/_prefill）的，走 __getattr__ 的实例级透传根本不参与（D-fuzi-12）。
    cls = _bridge_class()
    obj = cls.__new__(cls)
    ChatGLMCacheBridge.__init__(obj, model)
    return obj


_BRIDGE_CLS = None


def _bridge_class() -> type:
    """构造 ``ChatGLMCacheBridge + GenerationMixin`` 的子类（幂等）。"""
    global _BRIDGE_CLS
    if _BRIDGE_CLS is not None:
        return _BRIDGE_CLS
    try:
        from transformers import GenerationMixin

        bases = (ChatGLMCacheBridge, GenerationMixin)
    except Exception:  # noqa: BLE001 — 老版 transformers 无需补
        bases = (ChatGLMCacheBridge,)
    _BRIDGE_CLS = type("ChatGLMCacheBridge", bases, {})
    return _BRIDGE_CLS


# ------------------------------------------------------- 4) 旧 tokenizer 的 _pad
def _patch_legacy_pad(tok_cls: type) -> bool:
    """给旧 tokenizer 类补一个能吃下 ``padding_side`` 的 ``_pad``（只补签名）。"""
    key = f"{tok_cls.__module__}.{tok_cls.__name__}"
    if key in _PAD_PATCHED:
        return False
    orig = getattr(tok_cls, "_pad", None)
    if orig is None:
        return False

    def _pad(self, encoded_inputs, max_length=None, padding_strategy=None,
             pad_to_multiple_of=None, return_attention_mask=None,
             **_ignored):  # noqa: ANN001
        kwargs = {}
        if max_length is not None:
            kwargs["max_length"] = max_length
        if padding_strategy is not None:
            kwargs["padding_strategy"] = padding_strategy
        if pad_to_multiple_of is not None:
            kwargs["pad_to_multiple_of"] = pad_to_multiple_of
        if return_attention_mask is not None:
            kwargs["return_attention_mask"] = return_attention_mask
        try:
            return orig(self, encoded_inputs, **kwargs)
        except TypeError:
            # 更老的签名：根本不接受 padding_strategy
            kwargs.pop("padding_strategy", None)
            return orig(self, encoded_inputs, **kwargs)

    tok_cls._pad = _pad  # type: ignore[method-assign]
    _PAD_PATCHED.add(key)
    return True


# --------------------------------------------------------------- 3) 适配 tokenizer
class ChatGLMTokenizerAdapter:
    """给 ChatGLM 系 tokenizer 加一层"安全调用"壳。

    目的有二：
      * ``__call__`` 永远走"先取 ids，再自己组 tensor"，**不经过**基类的
        ``prepare_for_model/pad`` 那条对旧代码不友好的路径；
      * 自动补 ChatGLM 生成所需的 ``[gMASK] <sop>`` 前缀。

    只暴露 llm_base 需要的方法，避免"半个 PreTrainedTokenizer"造成的隐性行为差异。
    """

    def __init__(self, inner, add_gmask: bool = True):
        self.inner = inner
        self.add_gmask = add_gmask

    # -- 透传常用属性 --------------------------------------------------
    def __getattr__(self, item):  # noqa: ANN001
        return getattr(self.inner, item)

    @property
    def name_or_path(self) -> str:
        return getattr(self.inner, "name_or_path", "")

    @property
    def eos_token_id(self):  # noqa: ANN201
        return getattr(self.inner, "eos_token_id", None)

    @property
    def pad_token_id(self):  # noqa: ANN201
        return getattr(self.inner, "pad_token_id", None)

    @property
    def chat_template(self):  # noqa: ANN201
        """显式返回 None：ChatGLM 走原生 [Round] 模板，不能走 chat_template。"""
        return None

    # -- 文本 → ids ----------------------------------------------------
    def _gmask_ids(self) -> list[int]:
        ids = self.inner.convert_tokens_to_ids(CFG["gmask"])
        if isinstance(ids, list):
            ids = ids[0] if ids else None
        return [int(ids)] if ids is not None else []

    def _sop_ids(self) -> list[int]:
        ids = self.inner.convert_tokens_to_ids(CFG["sop"])
        if isinstance(ids, list):
            ids = ids[0] if ids else None
        return [int(ids)] if ids is not None else []

    def encode_ids(self, text: str) -> list[int]:
        ids = list(self.inner.tokenize(text))
        out: list[int] = []
        if self.add_gmask:
            out.extend(self._gmask_ids())
            out.extend(self._sop_ids())
        out.extend(int(i) for i in self.inner.convert_tokens_to_ids(ids))
        return out

    def __call__(self, text, return_tensors: str | None = None, **kwargs):  # noqa: ANN001
        """只需支持"单条文本 → input_ids 张量"这一种用法（本仓库从不成批 padding）。

        返回值必须是 ``BatchEncoding``（而不是普通 dict）：上层（``llm_base``）
        会对它调用 ``.to(device)`` 把张量搬到模型设备上，普通 dict 没有 ``.to()``
        ——实测报 ``AttributeError: 'dict' object has no attribute 'to'``（D-fuzi-18）。
        """
        if isinstance(text, (list, tuple)):
            raise NotImplementedError("ChatGLMTokenizerAdapter 只支持单条文本输入")
        ids = self.encode_ids(text)
        if return_tensors == "pt":
            import torch
            from transformers.tokenization_utils_base import BatchEncoding

            t = torch.tensor([ids], dtype=torch.long)
            attn = torch.ones_like(t)
            return BatchEncoding({"input_ids": t, "attention_mask": attn},
                                 tensor_type="pt")
        return {"input_ids": ids, "attention_mask": [1] * len(ids)}

    # -- ids → 文本 ----------------------------------------------------
    def decode(self, ids, skip_special_tokens: bool = True, **kwargs) -> str:  # noqa: ANN001
        try:
            import torch

            if isinstance(ids, torch.Tensor):
                ids = ids.tolist()
        except Exception:  # noqa: BLE001
            pass
        return self.inner.decode(ids, skip_special_tokens=skip_special_tokens)

    def convert_ids_to_tokens(self, ids, skip_special_tokens: bool = False):  # noqa: ANN001
        return self.inner.convert_ids_to_tokens(ids, skip_special_tokens=skip_special_tokens)


# ------------------------------------------------------------------- 装载入口
@dataclass
class ChatGLMBundle:
    tokenizer: object
    model: object
    name: str
    path: str


def _resolve_dtype(dtype, torch_mod):  # noqa: ANN001
    """把配置里的 dtype 字符串翻成 torch dtype；auto = CUDA 用 fp16 / CPU 用 fp32。"""
    if dtype is None or str(dtype).lower() in ("", "auto"):
        return torch_mod.float16 if torch_mod.cuda.is_available() else torch_mod.float32
    if isinstance(dtype, str):
        return {"float16": torch_mod.float16, "fp16": torch_mod.float16,
                "half": torch_mod.float16, "float32": torch_mod.float32,
                "fp32": torch_mod.float32, "bfloat16": torch_mod.bfloat16}.get(
                    dtype.lower(), torch_mod.float32)
    return dtype


def load_chatglm(model_path: str, dtype="auto", trust_remote_code: bool = True,
                 device: str | None = None) -> ChatGLMBundle:
    """加载 ChatGLM 系模型（本仓库用于 fuzi-mingcha-v1_0）。"""
    import torch
    from transformers import AutoModel, AutoTokenizer

    _patch_sentencepiece_loader()

    tok = AutoTokenizer.from_pretrained(model_path, trust_remote_code=trust_remote_code)
    _patch_legacy_pad(type(tok))
    adapter = ChatGLMTokenizerAdapter(tok)

    td = _resolve_dtype(dtype, torch)
    # 必须用 AutoModel 而不是 AutoModelForCausalLM：后者的"模型类型白名单"不接受
    # 未注册的远程 config（报 Unrecognized configuration class ... ChatGLMConfig），
    # 而 config.json 的 auto_map["AutoModel"] 正指向 ChatGLMForConditionalGeneration。
    # 这也是 fuzi-mingcha 官方 README 的写法（D-fuzi-4）。
    # 先拿到远程类，补好 v5 接口（D-fuzi-7）再交给 from_pretrained。
    from transformers.dynamic_module_utils import get_class_from_dynamic_module

    try:
        model_cls = get_class_from_dynamic_module(
            "modeling_chatglm.ChatGLMForConditionalGeneration", model_path)
        _patch_tied_weights_keys(model_cls)
        model_cls = _patch_generation_mixin(model_cls)
    except Exception:  # noqa: BLE001 — 非 ChatGLM 目录或取不到远程类时照旧走 AutoModel
        model_cls = None

    if model_cls is not None:
        # 新版 GenerationMixin 预分配 KV cache 时要读 config.num_hidden_layers，
        # 而 ChatGLM 的 config 里叫 num_layers（D-fuzi-9）：补个别名，
        # 不改模型仓库里的 configuration_chatglm.py。
        _cfg = getattr(model_cls, "config_class", None)
        try:
            from transformers import AutoConfig

            cfg = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
            _alias_config_attrs(cfg)
        except Exception:  # noqa: BLE001
            cfg = None
        if cfg is not None:
            mdl = model_cls.from_pretrained(model_path, config=cfg, dtype=td)
        else:
            mdl = model_cls.from_pretrained(model_path, dtype=td)
    else:
        mdl = AutoModel.from_pretrained(model_path,
                                        trust_remote_code=trust_remote_code,
                                        dtype=td)
    if device and str(device).startswith("cuda"):
        mdl = mdl.to(device)
    mdl.eval()
    # KV cache 桥接：让新版 generate 与旧 ChatGLM 的元组缓存能对上（D-fuzi-10）
    mdl = wrap_chatglm(mdl)
    return ChatGLMBundle(tokenizer=adapter, model=mdl,
                         name=Path(model_path).name, path=str(model_path))


def is_chatglm_dir(model_path: str) -> bool:
    """目录是否是 ChatGLM 系（看 config.json 的 model_type/architectures）。"""
    import json

    cfg = Path(model_path) / "config.json"
    if not cfg.is_file():
        return False
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if str(d.get("model_type", "")).lower() == "chatglm":
        return True
    return any("ChatGLM" in str(a) for a in (d.get("architectures") or []))


__all__ = ["load_chatglm", "is_chatglm_dir", "ChatGLMBundle",
           "ChatGLMTokenizerAdapter", "_patch_sentencepiece_loader",
           "_patch_legacy_pad", "CFG"]
