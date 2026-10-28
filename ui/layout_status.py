"""标题栏布局提示的展示压缩，不改变后台定位或记牌状态。"""


def compact_layout_status(message: str) -> str:
    """将详细布局状态压缩到至多六个字符，详细内容保留在悬停提示。

    Args:
        message: 后端的完整状态信息。
    Returns:
        简短状态文字；空消息仍为空。
    Raises:
        TypeError: message不是字符串。
    """
    if not isinstance(message, str):
        raise TypeError('布局状态必须为字符串')
    if not message.strip():
        return ''
    rules = (
        (('未取得有效画面', '等待有效画面'), '等待画面'),
        (('手牌参照丢失',), '手牌丢失'),
        (('归属不确定',), '归属待确认'),
        (('出牌移动中', '等待位置稳定'), '等待稳定'),
        (('保留上一局', '等待新手牌'), '等待下局'),
        (('底牌区无牌',), '底牌暂缺'),
        (('等待底牌', '等待上方底牌', '底牌区未检测到'), '等待底牌'),
        (('手牌数量不合法', '手牌稳定', '定位手牌', '等待手牌定位'), '确认手牌'),
        (('正在跟踪', '记牌中'), '正在记牌'),
        (('已定位',), '定位完成'),
        (('已开局',), '已开局'),
    )
    for terms, text in rules:
        if any(term in message for term in terms):
            return text
    return '定位中' if '自动' in message else '识别中'
