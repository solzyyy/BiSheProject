
中华女子学院数据科学与信息技术学院
毕业设计（论文）中期检查

文字冒险游戏内容生成系统的设计与实现



作　　　者：赵银荧
院　　　系：数据科学与信息技术学院
专　　　业：数字媒体技术
年　　　级：2022级
学　　　号：220812055
指导教师：张洋
日期：



 

独创性声明
本人声明所呈交的论文是本人在指导教师指导下进行的研究工作及取得的研究成果。尽我所知，除了文中特别加以标注和致谢中所罗列的内容以外，论文中不包含其他人己经发表或撰写过的研究成果，也不包含为获得中华女子学院或其他教育机构的学位或证书而使用过的材料。与我一同工作的同志对本研究所做的任何贡献均己在论文中作了明确的说明并表示了谢意。
本人签名：                                      日期：___________________




关于论文使用授权的说明
本人完全了解中华女子学院有关保留和使用学位论文的规定，学生在校攻读学位期间论文工作的知识产权单位属中华女子学院。学校有权保留并向国家有关部门或机构送交论文的复印件和磁盘，允许学位论文被查阅和借阅；学校可以公布学位论文的全部或部分内容，可以允许采用影印、缩印或其它复制手段保存、汇编学位论文。
本学位论文不属于保密范围，适用本授权书。
本人签名：                                  日期：___________________
指导教师签名：                          日期：___________________



 
摘  要
文字冒险游戏因其分支剧情和多结局机制受到玩家青睐。然而，传统开发方式依赖人工编写大量分支剧本，面临成本高、一致性差、扩展困难等问题。大语言模型（Large Language Model，LLM）虽然具备文本生成能力，但在长篇分支叙事生成场景中，存在状态丢失、角色行为不一致、叙事结构松散等不足。
本文设计并实现了一种基于大语言模型与知识图谱的文字冒险游戏内容生成系统。系统以经典文学文本为输入，通过五阶段流水线实现从原始文本到可运行视觉小说游戏的端到端自动化生成：
（1）事件图谱构建。利用LLM从文本中提取结构化事件并构建Neo4j知识图谱。
（2）角色建模。通过实体消歧、三类状态提取和画像聚合建立角色模型。
（3）分支路径生成。基于主线记录和决策点分析，使用LangGraph有状态工作流编排分支路径，LLM通过函数调用（Function Calling）在工作流循环中自主决策，生成分支事件、合流到主线或创建提前结局。
（4）叙事扩写。利用LLM将简短场景描述扩写为文学性叙述，提升文本的艺术表现力与沉浸感。
（5）游戏工程生成。自动生成Ren'Py视觉小说脚本并导入美术音乐资源。
以《王佛脱险记》为实验文本，系统成功提取24个结构化事件，生成2个分支点和7种结局候选，最终输出可运行的视觉小说游戏。

关键词：大语言模型，知识图谱，互动叙事，分支路径生成，状态管理
 
Abstract
Text adventure games are favored by players for their branching narratives and multi-ending mechanisms. However, traditional development methods rely heavily on manual scriptwriting for numerous branching paths, facing challenges such as high costs, poor consistency, and difficulty in expansion. While Large Language Models (LLMs) possess text generation capabilities, they exhibit limitations including state loss, inconsistent character behavior, and loose narrative structures when applied to long-form branching narrative generation.
This paper designs and implements a content generation system for text adventure games based on LLMs and Knowledge Graphs. Using classic literary texts as input, the system achieves end-to-end automated generation from raw text to playable visual novel games through a five-stage pipeline:
(1) Event Graph Construction: Utilizing LLMs to extract structured events from text and constructing a Neo4j knowledge graph.
(2) Character Modeling: Establishing character models through entity disambiguation, extraction of three types of states, and persona aggregation.
(3) Branching Path Generation: Based on main-thread records and decision-point analysis, LangGraph is employed for stateful workflow orchestration. LLMs autonomously make decisions within the workflow loop via Function Calling, generating branching events, merging back to the main thread, or creating early endings.
(4) Narrative Expansion: Leveraging LLMs to expand concise scene descriptions into literary narratives.
(5) Script Generation and Deployment: Automatically generating Ren'Py visual novel scripts and importing art and music resources.
Using "Le Conte de Wang-Fô" as the experimental text, the system successfully extracted 24 structured events, generated 2 branching points and 7 ending candidates, and ultimately produced a playable visual novel comprising 23 Ren'Py scripts (4,182 lines).

Keywords: Large Language Models (LLMs), Knowledge Graph, Interactive Narrative, Branching Path Generation, State Management, LangGraph, Ren'Py



目  录
1 前言	6
1.1 研究背景	6
1.2 研究意义	6
2 相关工作与技术基础	7
2.1 互动叙事生成系统	7
2.2 关键技术	8
3 问题分析与方案论证	9
3.1 问题分析	9
3.2 方案比较与选择	9
3.3 方案调整说明	10
4 系统设计与实现	10
4.1 系统架构	10
	11
4.2 数据流设计	11
	12
4.3.1 事件图谱构建模块	12
4.3.2 角色建模模块	12
4.3.3 状态管理模块	13
4.3.4 分支生成模块	13
4.3.5 叙事扩写模块	14
4.3.6 Ren'Py脚本生成模块	14
5 测试与性能分析	15
6 结束语	15
参考文献【TODO：当前仅10篇，建议补充至15-20篇，特别是2023-2025年LLM+叙事生成方向的文献，以及知识图谱辅助NLP的相关工作】	15

 
1 引言
1.1 研究背景
互动叙事（Interactive Narrative）是数字游戏与人工智能交叉领域的重要研究方向【Riedl M, Bulitko V. Interactive Narrative: A Novel Application of Artificial Intelligence for Computer Games[C]. AAAI, 2012】。在互动叙事游戏中，玩家的选择能够影响故事走向，产生不同的分支路径和结局。文字冒险游戏作为互动叙事的典型形式，以剧本内容为核心。其通常设有分支和多个结局，玩家通过选择指令改变角色行动从而走向不同结局。代表性作品包括《心跳文学俱乐部》《海市蜃楼之馆》等，这类游戏因其文学性叙事和沉浸式体验受到广泛关注【王玉玊. 从千禧年走向未来——国产文字冒险游戏中的中国[J]. 文艺理论与批评, 2024; 钮侠梅. 文字冒险游戏设计中的沉浸式体验研究与实践[D]. 中国美术学院, 2020】。
然而，传统文字冒险游戏的开发面临以下核心问题：
（1）人工编写成本高。分支剧本需要编剧手工编写每条路径的对话和场景。一个包含N个决策点的故事理论上有2的N次方条路径，人工编写成本随之急剧上升。
（2）跨路径一致性难以保证。多条分支路径中，角色的性格、关系、世界状态需要保持逻辑一致，但人工维护大量平行状态极易出错。
（3）扩展性差。新增一个分支点可能影响后续所有路径，需要大量返工以确保衔接。
近年来，大语言模型（Large Language Model, LLM）的快速发展为自动化叙事生成提供了新的技术可能【Fang X, Ng D T, Leung J K, et al. A systematic review of artificial intelligence technologies used for story writing[J]. Education and Information Technologies, 2023; Alhussain A I, Azmi A M. Automatic story generation: A survey of approaches[J]. ACM Computing Surveys, 2021】。GPT-4、Gemini、DeepSeek等模型展现出强大的文本理解和生成能力。然而，直接使用LLM生成长篇分支故事仍存在明显不足：LLM缺乏显式的状态管理机制，长文本生成中容易遗忘先前设定；不同分支中同一角色可能出现性格矛盾；生成内容缺乏整体规划，容易偏离主题【Xi Y, Mao X, Li L, et al. Kuileixi: a chinese open-ended text adventure game[C]. ACL, 2021; Wang Q, et al. GenQuest: An LLM-based Text Adventure Game for Language Learners[J]. arXiv:2510.04498, 2025】。
与此同时，人类历史上积淀的经典文学资源浩如烟海，大量优秀作品尚未得到系统性的数字化互动开发。若能在经典文学文本与文字冒险游戏之间搭起桥梁，将使经典文学以更具沉浸感和互动性的形式重新走入大众视野。
1.2 研究意义
基于上述背景，本研究提出一种“知识图谱+状态驱动+LLM编排”的技术路线，核心思路是：不让LLM凭空创作故事，而是让LLM在结构化知识约束下进行有据可依的叙事扩展。
实用价值：系统实现了从文学文本到可运行游戏的完整自动化流水线，降低了互动叙事游戏的开发门槛，为独立开发者和小型团队提供了可行的内容生成工具，也为经典文学的数字化再创作提供了一条高效路径。
学术价值：系统探索了知识图谱与LLM结合的叙事生成框架，验证了三层状态模型与Function Calling驱动的状态管理方案在保证长篇叙事一致性方面的有效性，为人机协同创作领域的研究提供了参考。
1.3 本文的研究内容及组织结构
本文的主要研究内容包括四个部分：第一部分针对非结构化文学文本的结构化处理问题，研究并设计了基于大语言模型的事件图谱构建与角色建模方法；第二部分针对多分支路径中叙事一致性难以维护的问题，研究并设计了基于三层状态模型与LLM Function Calling的状态管理方案，以及基于LangGraph有状态工作流的分支路径自动生成方法；第三部分整合前述研究内容，完成了文字冒险游戏内容生成系统的设计与实现；第四部分则是对该系统的测试与评估。本文的研究整体框架如图1-1所示。
 
图1-1 论文研究整体框架图
论文的章节安排如下：
第1章：引言。本章主要介绍了本研究的背景与意义，分析了文字冒险游戏开发面临的核心问题以及大语言模型在互动叙事生成中的不足，明确了本研究的切入点和目标。最后，对论文的研究内容及组织结构进行了说明。
第2章：相关工作与技术基础。本章对互动叙事生成领域的已有研究进行了综述，包括规则驱动方法、AI角色模拟方法和LLM直接生成方法，分析了各方法的优势与局限。在此基础上，介绍了本系统所依赖的四项关键技术。
第3章：问题分析与方案论证。本章将核心问题分解为四个子问题，分析了子问题之间的逻辑关系。通过对三种代表性技术方案的系统比较，论证了选择“知识图谱+LLM+状态管理”方案的合理性。
第4章：系统设计与实现。本章首先介绍了系统的四层架构和五阶段数据流设计，然后详细描述了六个功能模块的设计与实现方法，包括事件图谱构建、角色建模、状态管理、分支生成、叙事扩写和Ren’Py脚本生成模块。
第5章：测试与性能分析。本章围绕系统的实际实现，进行功能测试和性能分析，展示实验结果并进行讨论。
第6章：结束语。本章对全文工作进行总结，列举主要贡献，分析系统局限性并提出未来改进方向。
2 相关工作与技术基础
2.1 互动叙事生成系统
互动叙事生成是人工智能领域的经典问题，经历了从规则驱动再到大语言模型驱动的演进过程。早期系统依赖预定义的规则和角色行为模型控制叙事走向【】，而近年来大语言模型的兴起为开放式叙事生成提供了新的可能【】。
规则驱动方法。早期互动叙事系统以规则和脚本为基础。Mateas和Stern于2005年开发的Facade系统是交互式戏剧的先驱，使用基于规则的戏剧管理器（Drama Manager）控制剧情走向【TODO：此处需补充Facade系统的原始文献引用，参考 Mateas M, Stern A. Facade: An experiment in building a fully-realized interactive drama[C]. 2005】。该方法需要设计者预先编写大量规则，扩展性受限。
AI角色模拟方法。Evans和Short于2013年开发的Versu系统采用基于AI的角色模拟，让虚拟角色根据其性格和社交规则自主行动，产生涌现式叙事【TODO：此处需补充Versu系统的原始文献引用，参考 Evans R, Short E. Versu—a simulationist storytelling engine[J]. IEEE Transactions on Computational Intelligence and AI in Games, 2013】。该方法适用于社交互动场景，但难以处理复杂的剧情结构。
LLM直接生成方法。2019年出现的AI Dungeon直接使用GPT-2（后升级为GPT-3）生成文字冒险游戏内容。该方法的优势在于灵活性和创造性，但存在严重的一致性问题——角色可能突然改变性格，已发生的事件可能被遗忘，世界设定可能自相矛盾。
事实上，上述一致性问题并非LLM时代的新挑战——互动叙事领域的学者早已指出结构化设计的重要性。珍妮特·默里（Janet Murray）在《全息甲板上的哈姆雷特》中提出，计算机作为叙事媒介需要在创造性与结构性之间取得平衡。在此基础上，艾斯本·阿尔萨斯（Espen Aarseth）在《赛博文本：遍历文学透视》中进一步提出了“遍历文学”概念，指出互动叙事的文本结构要求读者付出主动的探索努力才能完整遍历，这对自动生成系统的结构化能力提出了更高要求。
综上所述，规则驱动方法扩展性不足，AI角色模拟方法难以支撑复杂剧情结构，而LLM直接生成方法虽具备创造性但缺乏有效的状态管理与结构化约束机制。本系统结合结构化知识表示（知识图谱）与LLM的生成能力，通过事件图谱提供叙事骨架，通过三层状态模型维护一致性，通过LangGraph工作流编排生成过程，在保持LLM创造性的同时解决一致性问题。
2.2 关键技术
	本系统涉及四项关键技术：大语言模型负责内容理解与生成决策，知识图谱提供结构化的叙事约束，LangGraph编排多步骤生成工作流，Ren'Py将生成结果转化为可运行的游戏。以下分别介绍各项技术及其在本系统中的作用。
（1）大语言模型与Function Calling。本系统以DeepSeek V3为主要大语言模型，承担事件提取、状态管理、分支决策等核心任务；在叙事扩写阶段使用Gemini 2.5 Flash进行场景扩写与对话生成，以降低长文本生成的调用成本。其中，Function Calling是本系统依赖的关键能力，该机制允许模型在推理过程中调用预定义的外部函数。本系统利用这一机制，让LLM在理解事件语义后自主决定调用哪些状态更新函数，实现语义理解与程序逻辑的结合。
（2）知识图谱与Neo4j。本系统需要表示事件之间的因果关系、角色之间的社会关系等复杂关联，这类数据天然适合图结构建模。Neo4j作为原生图数据库，使用属性图模型存储节点和关系，其Cypher查询语言能够高效地完成多跳关联查询。本系统将事件、实体、状态变化存储于Neo4j知识图谱中，为LLM的叙事生成提供结构化的约束基础。
（3）LangGraph有状态工作流。分支路径生成需要LLM在循环中反复决策——判断是继续生成分支事件、合流到主线还是创建结局——传统的DAG（有向无环图）工作流无法表达这种循环结构。LangGraph是LangChain团队推出的工作流编排框架，支持循环执行、条件路由和检查点恢复，本系统使用它编排分支路径生成的完整决策流程。
（4）Ren'Py视觉小说引擎。本系统的最终输出形式为可运行的视觉小说游戏，因此需要一个成熟的游戏引擎作为载体。Ren'Py是开源的视觉小说引擎，使用自定义脚本语言描述对话、选择和场景切换，原生支持多分支多结局、存档回退等游戏机制。本系统自动将生成的叙事内容转换为Ren'Py脚本格式，直接输出可运行的游戏。
3 问题分析与方案论证
3.1 问题分析
本研究需要解决的核心问题是：如何利用LLM从文学文本自动生成多分支、多结局的互动叙事游戏，同时保证跨路径的叙事一致性和角色行为连贯性。该问题可分解为以下子问题：
（1）如何从非结构化文本中提取结构化的叙事元素（事件、角色、关系）；
（2）如何维护多条分支路径中角色状态和世界状态的一致性；
（3）如何让LLM在约束下自主决策分支路径的走向；
（4）如何将结构化数据自动转换为可运行的游戏脚本。
其中，子问题（1）是后续工作的基础，子问题（2）（3）是系统的核心难点，子问题（4）是最终输出环节。
 
图3-1 子问题关系图
3.2 方案比较与选择
针对上述问题，结合相关工作中互动叙事系统的技术路线，本文归纳并比较了三种可行方案：
方案	优点	缺点
纯LLM生成方案	简单灵活，创造性强	无状态管理，一致性差，不可控
规则引擎+模板	高度可控，一致性好	灵活性差，需大量规则，创造性不足
知识图谱+LLM+状态管理（本方案）	有据可依，一致性好，保留LLM创造性	实现复杂度较高，依赖LLM API
表1 三种方案比较
经过对比分析，本系统选择第三种方案。该方案通过知识图谱为LLM提供结构化的叙事约束，通过状态管理机制保证跨路径一致性，同时保留了LLM在内容生成方面的创造性优势。
4 系统设计与实现
4.1 系统架构
系统采用分层模块化架构，自上而下分为四层。
接口层为用户提供操作入口，基于typer和rich框架实现统一CLI，支持按步骤执行23个处理步骤，同时提供交互式菜单引导用户逐步完成完整流水线。
业务逻辑层实现五阶段内容生成流水线，包含事件图谱构建、人物建模、状态管理、分支生成、Ren'Py脚本生成五个核心模块，各模块之间通过标准化的数据格式传递中间结果。
核心基础设施层封装外部服务，提供统一的调用接口。该层包含异步LLM客户端、Neo4j图数据库客户端、文本处理器等通用组件，使上层业务模块无需关心具体的模型厂商或存储实现细节。
外部服务与存储层位于最底层，提供LLM推理能力和数据持久化能力，包括DeepSeek API、Gemini API、Neo4j图数据库以及本地JSON/YAML文件存储。
【TODO-图1：在此处插入系统架构图。建议画四层架构（自上而下）：接口层→业务逻辑层→核心基础设施层→外部服务与存储层，用箭头表示调用方向。】
4.2 数据流设计
系统的数据从原始文学文本到可运行游戏，经历以下五个阶段的处理：
（1）事件图谱构建阶段：原始文本经事件提取和关系提取后导入Neo4j图数据库，构建结构化的事件知识图谱。
（2）角色建模阶段：在事件图谱基础上进行人物提及（Mention）提取、实体消歧和三类状态提取，建立状态基线并聚合人物画像与静态人设。
（3）分支路径生成阶段：依次执行主线生成、决策点分析、结局候选确定、支线生成和LangGraph路径编排。
（4）叙事扩写阶段：对路径编排结果进行事件补全，利用LLM将简短场景描述扩写为文学性叙述和完整对话。
（5）脚本化呈现阶段：自动生成Ren'Py脚本并导入美术音乐资源，输出可直接运行的视觉小说游戏。
系统通过统一CLI按步骤编排上述23个处理步骤，也可通过交互式菜单逐步引导用户执行完整流水线。
【TODO-图2：在此处插入五阶段数据流图/管线流程图。从左到右或从上到下画出：事件图谱构建→角色建模→分支路径生成→叙事扩写→脚本化呈现，标注每个阶段的输入和输出数据格式。】
4.3 文字冒险游戏内容生成系统功能模块设计与实现
系统包含6个核心模块。各模块按五阶段流水线的顺序依次执行：事件图谱构建模块为后续所有模块提供结构化数据基础；人物建模模块在事件图谱上完成角色识别与状态提取；状态管理模块提供三层状态模型的更新与快照机制，供分支生成模块在路径编排过程中调用；叙事扩写模块对生成的路径进行文学性扩写；Ren'Py脚本生成模块将最终结果转换为可运行的游戏工程。以下分别介绍各模块的功能和关键实现方法。
【TODO-图3（建议）：在此处插入模块依赖关系图。展示6个模块之间的数据传递和调用关系。此图为建议添加，非必须。】
4.3.1 事件图谱构建模块
该模块负责从原始文学文本中提取结构化事件和事件间关系，并导入Neo4j知识图谱。实现流程如下：首先，文本处理器将全文按自然段落分割；然后，对每个段落异步调用LLM，提取事件的场景描述、原文引用、对话内容、涉及角色等结构化字段；接着，对相邻事件对再次调用LLM，识别事件间的因果关系和时序关系；最后，将事件节点及其关系写入Neo4j图数据库。
4.3.2 角色建模模块
该模块实现实体识别与消歧、三类状态提取、人物画像聚合和静态人设提取四项功能。
实体消歧采用两阶段策略：先通过LLM识别候选别名对，再通过上下文验证确认。例如，将皇帝、天子、陛下等归并为同一实体。实体表中的别名映射在Ren'Py脚本生成阶段被复用，用于统一对话中的角色称呼。
三类状态提取分别提取人物内在状态（情绪、动机、世界观等）、世界/物品状态（场景、环境、物品存在等）和人物关系状态（情感、权力动态、信任水平等）。每条状态变化记录包含target（目标）、dimension（维度）、old_value、new_value和trigger（触发事件）。
画像聚合从事件描述中提取行动词，使用TF-IDF（词频-逆文档频率）和PMI（Point-wise Mutual Information，点互信息）识别每个角色的独有行为模式。其中TF-IDF用于衡量行动词对角色的重要程度，PMI用于衡量行动词与角色的共现关联度。系统为每个角色提取叙事角色、社会位置、性格倾向、世界观和综合人设摘要五个静态属性。
4.3.3 状态管理模块
状态管理模块是保证叙事一致性的核心。该模块维护人物内在、人物关系、世界物品三层状态模型，通过LLM Function Calling驱动状态更新。
核心组件StateApplier在处理每个事件时，将事件信息、当前状态、候选状态变化打包为提示词，调用LLM的Function Calling功能。LLM可调用三个预定义函数：update_character_state()用于更新人物内在状态，update_relationship_state()用于更新关系状态，update_world_state()用于更新世界状态。LLM根据事件语义自主决定调用哪些函数以及传入什么参数，实现了语义理解与程序化状态更新的结合。每次状态更新后生成StateSnapshot（完整状态快照），为后续分支生成提供状态上下文。
4.3.4 分支生成模块
分支生成是系统最核心的模块，分为三个子阶段：主线生成、决策点分析与支线生成、LangGraph路径编排。
主线生成。从第一个事件开始逐步遍历事件知识图谱，对每个事件获取关联的状态变化，经LLM分类（世界事实型/可选型/条件型）和选择后，通过StateApplier应用状态更新并生成快照。主线代表无人干预下真实发生过的历史，是后续分支的参照基准。
决策点分析与支线生成。分析主线记录识别决策点，即原文中存在转折、冲突或选择的情节节点，为每个决策点生成替代选择方向和结局候选。
LangGraph路径编排。这是系统最核心的技术环节。系统使用LangGraph有状态工作流编排分支路径生成，采用混合模式：流程控制由LangGraph图结构管理，关键决策由LLM通过Function Calling完成，执行逻辑由确定性代码完成。
在工作流的决策节点中，LLM从三个工具函数中选择：生成新的分支事件、合流到主线、或创建提前结局。工作流支持检查点恢复，可从中断处继续生成。为防止长路径生成中的上下文溢出，系统在每次决策前检查消息历史长度并在必要时进行摘要压缩。
在状态一致性保障方面，分支路径中每个新事件都通过状态管理模块完成完整的状态更新流程。当分支路径合流回主线时，主线剩余事件也基于分支后的新状态重新生成状态变化，确保跨路径的状态连贯性。
【TODO-图4（建议）：在此处插入LangGraph状态流转图。展示工作流节点（决策、摘要压缩、状态更新等）和路由边（生成/合流/结局）。此图为建议添加，非必须。】
4.3.5 叙事扩写模块
该模块利用LLM将简短的scene_description扩写为文学性的详细场景描述和完整对话。关键设计包括：注入风格约束系统提示，抑制LLM的模板化修辞倾向，引导其生成更贴近文学表达的自然文本；自动推断叙述人称（第一/二/三人称），全文保持一致；支持增量处理，已增强的路径自动跳过，避免重复调用LLM。
4.3.6 Ren'Py脚本生成模块
该模块将增强后的路径数据自动转换为Ren'Py视觉小说脚本，包含四个生成器：CharacterGenerator生成角色定义（characters.rpy），自动关联立绘资源；PathScriptGenerator生成路径脚本（paths/*.rpy），包含场景切换、叙述文本、角色对话和选择菜单；EndingScriptGenerator生成结局脚本（endings/*.rpy）；MainScriptUpdater更新主脚本入口（script.rpy）。路径脚本中还自动插入Flavor选项（不影响剧情的互动选择），以提升游戏体验。
 
5 测试与性能分析
【TODO-第6章：终稿时补充完整内容。需包含以下部分： 6.1 测试环境：硬件配置、Python版本、Neo4j版本、LLM API版本等； 6.2 功能测试：逐模块验证输出正确性（事件提取准确率、实体消歧准确率、分支路径合理性等）； 6.3 性能分析：生成耗时、API调用次数与成本统计、生成质量评估； 6.4 实验结果展示：将第5章中移出的实验数据放在这里（24个事件、3个角色、2个分支点、4条路径等）； 6.5 结果分析与讨论。】
6 结束语
【TODO-第7章：终稿时补充完整内容。需包含以下部分： 7.1 工作总结：简要回顾整个系统的设计与实现工作； 7.2 主要贡献：列举本人独立完成的创新点和成果（如五阶段流水线、三层状态模型、LangGraph路径编排等）； 7.3 不足与展望：实事求是地评价系统局限性，提出未来改进方向。】
参考文献【TODO：当前仅10篇，建议补充至15-20篇，特别是2023-2025年LLM+叙事生成方向的文献，以及知识图谱辅助NLP的相关工作】
[1] Murray J H. Hamlet on the holodeck: The future of narrative in cyberspace[M]. MIT press, 2017.
[2] Aarseth E J. Cybertext: Perspectives on ergodic literature[M]. Johns Hopkins University Press, 1997.
[3] 王玉玊. 从千禧年走向未来——国产文字冒险游戏中的中国[J]. 文艺理论与批评, 2024(06): 156-169.
[4] 牛毅. 艾斯本·阿尔萨斯遍历文学理论研究[D]. 四川外国语大学, 2025.
[5] 邱子珅. 改编自印度史诗《摩诃婆罗多》的文字冒险游戏设计[D]. 上海交通大学, 2016.
[6] 杨睿. 基于剧本解析的文字冒险游戏渲染器研究与实现[D]. 中国传媒大学, 2022.
[7] Wang Q, Labib A, Swier R, et al. GenQuest: An LLM-based Text Adventure Game for Language Learners[J]. arXiv preprint arXiv:2510.04498, 2025.
[8] Xi Y, Mao X, Li L, et al. Kuileixi: a chinese open-ended text adventure game[C]//Proceedings of the 59th Annual Meeting of the Association for Computational Linguistics, 2021: 175-184.
[9] Schell J. The art of game design: A book of lenses[M]. CRC Press, 2008.
[10] 钮侠梅. 文字冒险游戏设计中的沉浸式体验研究与实践[D]. 中国美术学院, 2020.
