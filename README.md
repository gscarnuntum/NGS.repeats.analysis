# NGS.repeats.analysis

NGS.repeats.analysis is a pipeline for mapping and quality control of NGS datasets with focus on repeat detection. Mapping and repeat coverage follow the guidelines from [Teissandier, 2019](https://mobilednajournal.biomedcentral.com/articles/10.1186/s13100-019-0192-1).

The following experiments (protocols) are currently supported: RNA-seq, ChIP-seq, Cut&Run, Cut&Tag, ATAC-sequencing

#Installation

The pipeline requires installation in a fresh conda environment. The following steps are necessary:

1. Create fresh conda environment

    conda env create -n ngs Python=3.9
    conda activate ngs
    conda update -n base -c defaults conda

2. Install python packages

    conda install -c bioconda samtools
    conda install -c bioconda bedtools
    conda install -c bioconda bowtie2
    conda install -c bioconda fastqc
    conda install -c bioconda subread
    conda install -c bioconda trimmomatic
    conda install -c bioconda picard
    conda install -c bioconda deeptools
    conda install -c bioconda star


3. Install R libraries

open terminal an start R

    install.packages("argparser")
    install.packages("pepr")
    install.packages("reshape2")
    install.packages("ggplot2")

    if (!require("BiocManager", quietly = TRUE))
        install.packages("BiocManager")

    BiocManager::install("SummarizedExperiment")

4. Install Looper

    python -m pip install looper
    python -m pip install piper
