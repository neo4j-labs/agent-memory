"""Scientific schema: research papers and academic publications.

The scientific schema includes author, institution, method, dataset, metric,
concept and tool.

Sample data: synthetic ML paper abstracts. Papers, results, authors and grants
are invented; real library and hardware names are kept so the schema has
recognisable `tool` spans.
"""

from __future__ import annotations

from samples.base import STREAMING, Document, Highlight, SampleSet

DOCUMENTS = (
    Document(
        title="Paper 1: Efficient Transformer Architectures for Long-Context Understanding",
        meta="Venue: a machine-learning conference, 2024",
        content="""
        Abstract: We present LongFormer-X, a novel transformer architecture
        designed for efficient processing of documents exceeding 100,000 tokens.
        Building on prior sparse-attention work, our approach introduces a
        hierarchical attention mechanism that reduces computational complexity
        from O(n^2) to O(n log n).

        We evaluate LongFormer-X on two public long-context benchmarks and a new
        dataset we introduce called BookSum-Extended. Our model achieves
        state-of-the-art results on all benchmarks while using 40% less memory
        than comparable approaches.

        Key contributions: (1) A novel chunked attention pattern with learned
        routing, (2) An efficient implementation using fused attention kernels, and
        (3) A comprehensive ablation study demonstrating the importance of
        each component.

        Our code and pretrained models are available on GitHub. All experiments
        were conducted on NVIDIA A100 GPUs at the Brightwater AI Lab.

        Authors: Sarah Chen (Brightwater University), Michael Roberts
        (Corvus Research), and Yuki Tanaka (Kitahama Institute).
        """,
    ),
    Document(
        title="Paper 2: Charter Learning: Training Language Models with Principles",
        meta="Venue: a machine-learning conference, 2024",
        content="""
        Abstract: We introduce Charter Learning, a method for training language
        models to follow a set of principles without extensive human feedback
        annotation. Unlike RLHF (Reinforcement Learning from Human Feedback),
        Charter Learning requires only a charter of principles written in
        natural language.

        Our experiments on an in-house model family demonstrate that Charter
        Learning reduces harmful outputs by 73% compared to baseline, as measured
        on two public safety evaluation suites. We also evaluate on a standard
        knowledge benchmark to ensure capability is preserved.

        The training pipeline uses a combination of supervised fine-tuning on
        principle-following demonstrations and a novel critique-revision loop.
        We implement this using PyTorch and the HuggingFace Transformers library.

        Our analysis reveals that the effectiveness of Charter Learning depends
        critically on the specificity and comprehensiveness of the charter
        principles. We provide guidelines for practitioners based on experiments
        with over 200 different charter variants.

        Authors: Ada Whitfield (Helix Safety Labs), Amanda Askwith (Helix Safety
        Labs), Tom Brennan (Northlight AI), and Jan Leikens (Northlight AI).

        Acknowledgments: This research was supported by grants from the Meridian
        Science Fund and donated compute resources.
        """,
    ),
    Document(
        title="Paper 3: Graph Neural Networks for Drug Discovery: A Survey",
        meta="Venue: a machine-intelligence journal, 2024",
        content="""
        Abstract: This survey provides a comprehensive overview of graph neural
        network (GNN) applications in drug discovery. We review 247 papers
        published between 2018 and 2024, categorizing methods by their primary
        task: molecular property prediction, drug-target interaction, de novo
        design, and reaction prediction.

        Key architectures discussed include Message Passing Neural Networks
        (MPNNs), Graph Attention Networks (GATs), and Equivariant Graph Neural
        Networks (EGNNs). We benchmark 15 representative methods on three public
        molecular benchmark collections.

        Our analysis reveals that GNN performance on molecular property prediction
        has plateaued, with newer architectures providing diminishing returns.
        However, we identify promising directions in geometric deep learning
        and multi-modal approaches combining molecular graphs with protein
        structures.

        We release DrugGNN-Bench, a standardized evaluation framework implemented
        in PyTorch Geometric with support for 50+ GNN architectures and 30+
        benchmark datasets.

        Authors: Lisa Wang (Brightwater University), David Park (Queensbridge
        Medical School), and Carlos Ramos (Corvus Research).

        This work was supported by a public research grant and conducted in
        collaboration with two pharmaceutical partners.
        """,
    ),
)

USE_CASES = """
    1. Citation Network Analysis:
       - Track author collaborations
       - Identify research clusters by institution
       - Find influential papers by citation

    2. Method Genealogy:
       - Track evolution of techniques (RLHF -> Charter Learning)
       - Identify foundational methods
       - Discover method combinations

    3. Dataset Discovery:
       - Find papers using specific benchmarks
       - Track dataset adoption over time
       - Identify gaps in evaluation

    4. Tool Ecosystem:
       - Map framework dependencies
       - Track library adoption
       - Identify hardware requirements

    5. Collaboration Recommendations:
       - Find researchers with complementary expertise
       - Identify potential institutional partnerships
       - Discover cross-disciplinary opportunities
"""

SAMPLE = SampleSet(
    schema_name="scientific",
    title="Research Paper Analysis",
    blurb="Synthetic machine-learning paper abstracts.",
    threshold=0.4,
    documents=DOCUMENTS,
    highlights=(
        Highlight("Authors", "PERSON"),
        Highlight("Institutions", "ORGANIZATION"),
        Highlight("Methods & Techniques", "OBJECT", subtypes=("METHOD", "CONCEPT")),
        Highlight("Datasets & Benchmarks", "OBJECT", subtypes=("DATASET",)),
        Highlight("Tools & Frameworks", "OBJECT", subtypes=("TOOL",)),
        Highlight("Metrics", "OBJECT", subtypes=("METRIC",), limit=5),
    ),
    use_cases=USE_CASES,
    source_tag="research_papers",
    readback_query="graph neural network benchmark",
    demos=(STREAMING,),
    per_type_limit=6,
)
