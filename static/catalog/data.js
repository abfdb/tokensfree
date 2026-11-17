// 人工核验的信息快照；不是实时额度查询。
const RESOURCES = [
['bailian','阿里云百炼','试用赠金','模型 API','新人模型试用额度','需符合新人、地域与实名认证要求；不同模型额度独立。','到期或用完后可能计费，请在官方控制台确认用完即停设置。','https://help.aliyun.com/zh/model-studio/new-free-quota','https://bailian.console.aliyun.com/'],
['openrouter','OpenRouter','免费 API','文本 / 编程','提供 :free 模型','免费模型有请求频率与每日限额，模型可变。','只选择标有免费价格的模型；收费模型与更高额度另有条件。','https://openrouter.ai/collections/free-models','https://openrouter.ai/'],
['groq','Groq','免费 API','快速推理','提供免费计划','依模型与组织设置请求数及 Token 限额。','用量以官方控制台为准；不要把限速理解成无限免费。','https://console.groq.com/docs/rate-limits','https://console.groq.com/'],
['gemini','Gemini API','免费 API','多模态 / 文本','部分模型支持免费层','需核实服务地区、项目资格与模型限额。','免费层数据处理条款需单独阅读，勿直接上传公司机密。','https://ai.google.dev/gemini-api/docs/pricing','https://aistudio.google.com/'],
['colab','Google Colab','GPU / Notebook','实验 / Notebook','有限免费计算资源','GPU 类型、可用性和使用时长不保证，限制会动态变化。','不作为常驻生产服务器；禁止多账号规避限额。','https://research.google.com/colaboratory/faq.html','https://colab.research.google.com/'],
['zerogpu','Hugging Face ZeroGPU','GPU / Notebook','交互式模型演示','共享动态 GPU','存在排队和每日配额；托管资格与使用资格不同。','不是独占 GPU，也不是无限时长服务器；查看最新资格要求。','https://huggingface.co/docs/hub/spaces-zerogpu','https://huggingface.co/spaces'],
['hfinference','Hugging Face Inference','周期额度','模型 API','月度推理试用积分','额度取决于账号计划与官方现行规则。','超出积分可能产生费用；专用 Endpoints 是另一种付费服务。','https://huggingface.co/docs/inference-providers/pricing','https://huggingface.co/settings/billing'],
['cloudflare','Cloudflare Workers AI','周期额度','应用推理','每日免费 Neurons 配额','模型可用性受计划限制，部分模型需要付费计划。','付费计划超额按量收费；Neurons 不能直接等同 Token。','https://developers.cloudflare.com/workers-ai/platform/pricing/','https://dash.cloudflare.com/'],
['copilot','GitHub Copilot Free','周期额度','开发助手','个人免费方案','额度与模型使用范围按当前官方计划规则执行。','这是开发助手权益，不是可自由调用的通用模型 API 额度。','https://docs.github.com/en/copilot/concepts/billing-and-usage/individuals/billing','https://github.com/features/copilot'],
['hfgrant','Hugging Face 社区资助','申请制资助','开源演示','可申请社区 GPU 资助','需要提交有价值的 Space；由平台审核，不保证获批。','申请机会不是已领取额度，请勿提前计入可用算力。','https://huggingface.co/docs/hub/spaces-overview','https://huggingface.co/spaces']
].map(([id,name,type,use,offer,eligibility,risk,source,url])=>({id,name,type,use,offer,eligibility,risk,source,url,checked:'2026-11-17'}));
if(typeof module !== 'undefined') module.exports = RESOURCES;
