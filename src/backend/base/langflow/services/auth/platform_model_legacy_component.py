"""Fixed platform model node. It has no credentials or independent network access."""

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, IntInput, MessageTextInput, Output, SliderInput
from lfx.schema.message import Message


class PlatformModelComponent(Component):
    display_name = "平台模型"
    description = "由 deep_agents 校验权限并调用租户模型连接。密钥不会进入流程。"
    name = "PlatformModel"
    icon = "BrainCircuit"
    inputs = [
        MessageTextInput(name="input_value", display_name="输入文本"),
        DropdownInput(
            name="connection_id",
            display_name="平台模型连接",
            options=[],
            refresh_button=True,
            real_time_refresh=True,
            info="刷新读取有权查看的租户模型连接。选项值为连接 ID。",
        ),
        DropdownInput(
            name="model_name",
            display_name="模型名称",
            options=[],
            combobox=True,
            refresh_button=True,
            info="选择平台目录中的模型或填写连接支持的模型名称。更换连接后请核对模型名称。",
        ),
        SliderInput(
            name="temperature", display_name="Temperature", value=0, range_spec={"min": 0, "max": 2, "step": 0.1}
        ),
        IntInput(name="max_tokens", display_name="最大输出 Token", value=1024),
    ]
    outputs = [Output(display_name="模型文本", name="text", method="text_response")]

    async def text_response(self) -> Message:
        from langflow.services.auth.platform_models import invoke_current

        # 仅由专用已保存版本执行器注入调用上下文, 原生运行没有上下文时一律拒绝。
        text = await invoke_current(
            self.connection_id, self.model_name, self.input_value, self.temperature, self.max_tokens
        )
        return Message(text=text)
