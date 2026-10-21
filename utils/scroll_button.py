"""滚到最新消息的悬浮按钮（Scroll-to-Latest）。

================================================================================
【为什么需要单独一个组件】
    这个按钮必须在**用户页面层**工作：要监听滚动位置、要浮在右下角、
    点了要平滑滚动到底部，而且**不能触发 Streamlit 重跑**（否则每滚一下都重算整页）。
    Streamlit 的原生组件做不到这些。

【⚠️ 踩过的坑：为什么不用 components.html】
    最初用 ``st.components.v1.html`` 实现，结果**按钮根本不出现**。原因：
        1. 它把内容放进 **iframe**，脚本活在 iframe 里，操作父页面 DOM 很别扭；
        2. 为了不占布局空间我传了 ``width=0``，iframe 可能压根没渲染出来；
        3. 它在新版 Streamlit 里已经**废弃**（官方建议改用 st.iframe / st.html）。

    改用 ``st.html(..., unsafe_allow_javascript=True)`` 后问题消失：
    它是**非 iframe** 的，HTML 与脚本直接进入真实页面，能直接定位滚动容器、
    直接挂 fixed 按钮，也没有尺寸限制。

【它怎么工作】
    1. 找到"真正能滚动的那个容器"——不写死选择器，
       而是遍历候选元素，挑第一个 scrollHeight > clientHeight 的（见 findScroller）
    2. 监听滚动：只要不在底部（容差 80px）就显示按钮
    3. 点按钮 → smooth 滚动到底部 → 按钮隐藏
    4. 轮询检测新消息（元素数量变化）→ 在按钮上显示未读数

【为什么不写死 [data-testid="stAppViewContainer"]】
    Streamlit 内部 test id 会随版本变化。运行时探测"谁真的能滚"更耐用。

【降级行为】
    找不到滚动容器 / 页面不可访问时，只是**什么都不显示**，不报错、不影响聊天。
================================================================================
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

__all__ = ["BUTTON_ID", "render_scroll_to_latest"]

#: 按钮的 DOM id（同一页面只应存在一个）
BUTTON_ID: Final[str] = "chef-scroll-latest-btn"

#: 按钮文案
_BUTTON_LABEL: Final[str] = "↓"

#: 注入的 HTML + 脚本。
#: 通过 st.html(unsafe_allow_javascript=True) 注入，**运行在真实页面**（非 iframe），
#: 因此这里的 document 就是应用页面本身。
_HTML_TEMPLATE = """
<div id="{button_id}-anchor" style="display:none"></div>
<script>
(function () {
  const DOC = document;
  const BTN_ID = "{button_id}";
  const BOTTOM_TOLERANCE = 80;   // 距底部 80px 内算"已在底部"，不弹按钮

  // ---------- 1. 找出真正能滚动的容器 ----------
  //
  // ★ 这一段是本次踩坑的核心（真实浏览器实测才查出来的）：
  //   Streamlit 1.65 的主滚动容器是
  //       section[data-testid="stAppScrollToBottomContainer"]   （overflow-y: auto）
  //   而它外层 [data-testid="stAppViewContainer"] 是 overflow: hidden，**永远不滚**。
  //   之前只找 stAppViewContainer，判定"始终在底部"，于是按钮永不显示。
  //
  //   为了不再依赖具体版本的 test id，这里按三段式找：
  //     ① 已知的候选选择器（含 Streamlit 自己的滚动容器）
  //     ② 通用兜底：遍历所有元素，挑"overflowY 可滚 且 内容确实超出"的那个
  //     ③ 再退一步：整页滚动（老版本布局）
  function findScroller() {
    const known = [
      DOC.querySelector('[data-testid="stAppScrollToBottomContainer"]'),
      DOC.querySelector('[data-testid="stMain"]'),
      DOC.querySelector('[data-testid="stAppViewContainer"]'),
      DOC.querySelector('section.main'),
      DOC.querySelector('[data-testid="stMainBlockContainer"]'),
    ].filter(Boolean);

    // ① 已知选择器里挑真正能滚的
    for (const el of known) {
      const scrollable = el.scrollHeight - el.clientHeight;
      if (scrollable > 40) { return el; }
    }

    // ② 通用兜底：谁 overflow 是 auto/scroll 且内容超出，就是它
    let best = null;
    let bestOverflow = 0;
    const all = DOC.querySelectorAll('section, div, main');
    for (const el of all) {
      const canScroll = el.scrollHeight - el.clientHeight;
      if (canScroll <= 40) { continue; }
      const overflowY = window.getComputedStyle(el).overflowY;
      if (overflowY !== 'auto' && overflowY !== 'scroll') { continue; }
      // 排除侧边栏（那是另一个独立滚动区）
      if (el.closest('[data-testid="stSidebar"]')) { continue; }
      if (canScroll > bestOverflow) { bestOverflow = canScroll; best = el; }
    }
    if (best) { return best; }

    // ③ 最后退回整页滚动
    return DOC.scrollingElement || DOC.documentElement;
  }

  // ---------- 2. 创建/复用按钮 ----------
  function ensureButton() {
    let btn = DOC.getElementById(BTN_ID);
    if (btn && btn.isConnected) { return btn; }

    btn = DOC.createElement('button');
    btn.id = BTN_ID;
    btn.type = 'button';
    btn.setAttribute('aria-label', '滚动到最新消息');
    btn.title = '滚动到最新消息';
    btn.textContent = '{label}';
    Object.assign(btn.style, {
      position: 'fixed',
      right: '26px',
      bottom: '118px',
      minWidth: '40px',
      height: '40px',
      borderRadius: '20px',
      border: '1px solid #e5e7eb',
      background: '#ffffff',
      color: '#1f2328',
      fontSize: '16px',
      lineHeight: '38px',
      textAlign: 'center',
      cursor: 'pointer',
      boxShadow: '0 2px 10px rgba(0,0,0,.12)',
      zIndex: '99999',
      display: 'none',
      padding: '0 12px',
      fontFamily: 'inherit',
      transition: 'opacity .15s ease',
    });
    btn.addEventListener('mouseenter', function () { btn.style.background = '#f1f3f5'; });
    btn.addEventListener('mouseleave', function () { btn.style.background = '#ffffff'; });
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      const scroller = findScroller();
      if (scroller) {
        try {
          scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
        } catch (e) {
          scroller.scrollTop = scroller.scrollHeight;
        }
      }
      unread = 0;
      update();
    });
    DOC.body.appendChild(btn);
    return btn;
  }

  // ---------- 3. 状态 ----------
  let unread = 0;
  let lastCount = 0;

  function messageCount() {
    return DOC.querySelectorAll('[data-testid="stChatMessage"]').length;
  }

  function atBottom(el) {
    if (!el) { return true; }
    return (el.scrollHeight - el.scrollTop - el.clientHeight) <= BOTTOM_TOLERANCE;
  }

  function update() {
    const scroller = findScroller();
    const btn = ensureButton();

    const now = messageCount();
    if (lastCount === 0) { lastCount = now; }
    else if (now > lastCount) { unread += (now - lastCount); lastCount = now; }
    else { lastCount = now; }

    const shouldShow = messageCount() > 0 && !atBottom(scroller);
    if (shouldShow) {
      btn.textContent = unread > 0 ? ('{label} ' + unread) : '{label}';
      btn.style.display = 'block';
    } else {
      if (!atBottom(scroller)) { /* 保留未读数 */ } else { unread = 0; }
      btn.style.display = 'none';
    }
  }

  // ---------- 4. 绑定（只绑一次）----------
  ensureButton();
  if (!window.__chefScrollBound__) {
    window.__chefScrollBound__ = true;
    DOC.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
  }
  update();
  if (window.__chefScrollTimer__) { clearInterval(window.__chefScrollTimer__); }
  window.__chefScrollTimer__ = setInterval(update, 1000);
})();
</script>
"""


def render_scroll_to_latest() -> None:
    """在页面右下角挂一个「滚动到最新消息」的悬浮按钮。

    按钮只在"用户向上翻看历史、视图不在底部"时出现，点击后平滑滚到底部并隐藏。
    有新消息到达且用户不在底部时，按钮上显示未读条数。

    实现要点：用 ``st.html(unsafe_allow_javascript=True)`` 注入（**非 iframe**），
    脚本直接运行在应用页面里，能定位真实滚动容器并挂 fixed 按钮。
    """
    html = _HTML_TEMPLATE.replace("{button_id}", BUTTON_ID).replace("{label}", _BUTTON_LABEL)

    try:
        import streamlit as st

        st.html(html, unsafe_allow_javascript=True)
    except TypeError:
        # 老版本 Streamlit 没有 unsafe_allow_javascript 参数 → 退回 components.html
        try:
            import streamlit.components.v1 as components

            components.html(html, height=0, width=0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("滚动按钮注入失败（不影响聊天功能）：%s", exc)
    except Exception as exc:  # noqa: BLE001 - 装饰性功能，失败绝不能影响聊天
        logger.debug("滚动按钮注入失败（不影响聊天功能）：%s", exc)

