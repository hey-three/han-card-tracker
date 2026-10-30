"""在CPU版主进程及spawn子进程导入配置前声明版本能力。"""

import sys

setattr(sys, '_han_cpu_only', True)
