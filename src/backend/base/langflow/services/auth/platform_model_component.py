"""Fixed platform model node. It has no credentials or independent network access."""

from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, IntInput, MessageTextInput, Output, SliderInput
from lfx.schema.message import Message


class PlatformModelComponent(Component):
    display_name = "平台模型"
    description = "使用企业默认模型或指定企业模型记录,由平台校验权限并代调用。"
    name = "PlatformModel"
    icon = "BrainCircuit"
    inputs = [
        MessageTextInput(name="input_value", display_name="输入文本"),
        DropdownInput(
            name="selection_mode",
            display_name="模型选择",
            options=["tenant_default", "explicit"],
            options_metadata=[{"description": "跟随企业默认模型"}, {"description": "指定企业模型"}],
            value="tenant_default",
            real_time_refresh=True,
            refresh_button=True,
            info="跟随默认在每次执行时解析;指定模型保存企业模型记录 ID。",
        ),
        DropdownInput(
            name="connection_model_id",
            display_name="企业模型",
            options=[],
            value="",
            show=False,
            refresh_button=True,
            real_time_refresh=True,
            info="仅显示当前身份有权查看的已启用企业模型;模型与渠道由平台统一管理。",
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
            "",
            "",
            self.input_value,
            self.temperature,
            self.max_tokens,
            selection_mode=self.selection_mode,
            connection_model_id=self.connection_model_id,
        )
        return Message(text=text)
