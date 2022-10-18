#!/usr/bin/env python3
"""
ChIP-seq repeats analysis
"""

__auhor__ = ["Gunnar Schotta"]
__email__ = "gunnar.schotta@bmc.med.lmu.de"
__version__ = "0.0.1"


from argparse import ArgumentParser
import os
import re
import sys
import subprocess
import yaml
import pypiper
from pypiper import build_command

PROTOCOLS = ["ChIP", "C&T", "C&R", "RNA", "ATAC"]

"""
Main pipeline process.
"""
parser = ArgumentParser(description='ChIP-seq repeats analysis version ' + __version__)
parser = pypiper.add_pypiper_args(parser, groups=
    ['pypiper', 'looper', 'ngs'],
    required=["input", "genome", "sample-name", "output-parent"])
parser.add_argument("--protocol", dest="protocol", type=str,
                        default=None, choices=PROTOCOLS,
                        help="NGS processing protocol.")
args = parser.parse_args()
args.paired_end = args.single_or_paired.lower() == "paired"

if not args.input:
    parser.print_help()
    raise SystemExit

# Initialize, creating global PipelineManager and NGSTk instance for
# access in ancillary functions outside of main().
outfolder = os.path.abspath(os.path.join(args.output_parent, args.sample_name))

global pm
pm = pypiper.PipelineManager(
    name="ChIP-seq repeat analysis", outfolder=outfolder, args=args, version=__version__)

global ngstk
ngstk = pypiper.NGSTk(pm=pm)

# Convenience alias
tools = pm.config.tools
param = pm.config.parameters
param.outfolder = outfolder
res = pm.config.resources


############################################################################
#          Check that the input file(s) exist before continuing            #
############################################################################
if os.path.isfile(args.input[0]) and os.stat(args.input[0]).st_size > 0:
    print("Local input file: " + args.input[0])
elif os.path.isfile(args.input[0]) and os.stat(args.input[0]).st_size == 0:
    # The read1 file exists but is empty
    err_msg = "File exists but is empty: {}"
    pm.fail_pipeline(IOError(err_msg.format(args.input[0])))
else:
    # The read1 file does not exist
    err_msg = "Could not find: {}"
    pm.fail_pipeline(IOError(err_msg.format(args.input[0])))

if args.input2:
    if (os.path.isfile(args.input2[0]) and
            os.stat(args.input2[0]).st_size > 0):
        print("Local input file: " + args.input2[0])
    elif (os.path.isfile(args.input2[0]) and
            os.stat(args.input2[0]).st_size == 0):
        # The read1 file exists but is empty
        err_msg = "File exists but is empty: {}"
        pm.fail_pipeline(IOError(err_msg.format(args.input2[0])))
    else:
        # The read1 file does not exist
        err_msg = "Could not find: {}"
        pm.fail_pipeline(IOError(err_msg.format(args.input2[0])))

container = None  # legacy

############################################################################
#                      Grab and prepare input files                        #
############################################################################
pm.report_result(
    "File_mb",
    round(ngstk.get_file_size(
          [x for x in [args.input, args.input2] if x is not None])), 2)
pm.report_result("Read_type", args.single_or_paired)
pm.report_result("Genome", args.genome_assembly)

############################################################################
# 				Analysis pipeline 			   #
############################################################################

# Each (major) step should have its own subfolder
raw_folder = os.path.join(param.outfolder, "raw")
fastq_folder = os.path.join(param.outfolder, "fastq")

pm.timestamp("### Merge/link and fastq conversion: ")
# This command will merge multiple inputs so you can use multiple
# sequencing lanes in a single pipeline run.
local_input_files = ngstk.merge_or_link(
    [args.input, args.input2], raw_folder, args.sample_name)
# flatten nested list
if any(isinstance(i, list) for i in local_input_files):
    local_input_files = [i for e in local_input_files for i in e]
# maintain order and remove duplicate entries
local_input_files = list(dict.fromkeys(local_input_files))

cmd, out_fastq_pre, unaligned_fastq = ngstk.input_to_fastq(
    local_input_files, args.sample_name, args.paired_end, fastq_folder,
    zipmode=True)

# flatten nested list
if any(isinstance(i, list) for i in unaligned_fastq):
    unaligned_fastq = [i for e in unaligned_fastq for i in e]
# maintain order and remove duplicate entries
if any(isinstance(i, dict) for i in local_input_files):
    unaligned_fastq = list(dict.fromkeys(unaligned_fastq))

pm.run(cmd, unaligned_fastq,
       follow=ngstk.check_fastq(
        local_input_files, unaligned_fastq, args.paired_end))
pm.clean_add(out_fastq_pre + "*.fastq", conditional=True)

if args.paired_end:
    untrimmed_fastq1 = unaligned_fastq[0]
    untrimmed_fastq2 = unaligned_fastq[1]
else:
    untrimmed_fastq1 = unaligned_fastq
    untrimmed_fastq2 = None
    
# Also run a fastqc (if installed/requested)
fastqc_folder = os.path.join(param.outfolder, "fastqc")
fastqc_report = os.path.join(fastqc_folder,
    args.sample_name + "_R1_fastqc.html")
fastqc_report_R2 = os.path.join(fastqc_folder,
    args.sample_name + "_R2_fastqc.html")
if ngstk.check_command(tools.fastqc):
        ngstk.make_dir(fastqc_folder)
if fastqc_folder and os.path.isabs(fastqc_folder):
    ngstk.make_sure_path_exists(fastqc_folder)
cmd = (tools.fastqc + " --noextract --outdir " +
       fastqc_folder + " " + untrimmed_fastq1)
pm.run(cmd, fastqc_report, nofail=False)
pm.report_object("FastQC report r1", fastqc_report)
if args.paired_end and untrimmed_fastq2:
    cmd = (tools.fastqc + " --noextract --outdir " +
           fastqc_folder + " " + untrimmed_fastq2)
pm.run(cmd, fastqc_report_R2, nofail=False)
pm.report_object("FastQC report r2", fastqc_report_R2)


############################################################################
#                     Adapter trimming  for C&T/C&R/ATAC                   #
############################################################################
if args.protocol in ["ATAC", "C&R", "C&T"]:
    pm.timestamp("### Adapter trimming: ")

    res.adapters = res.adapters

    # Create names for trimmed FASTQ files.

    trimming_prefix = os.path.join(fastq_folder, args.sample_name)
    trimmed_fastq = trimming_prefix + "_R1_trim.fastq.gz"
    trimmed_fastq_R2 = trimming_prefix + "_R2_trim.fastq.gz"
    fastqc_folder = os.path.join(param.outfolder, "fastqc")
    fastqc_report = os.path.join(fastqc_folder,
        trimming_prefix + "_R1_trim_fastqc.html")
    fastqc_report_R2 = os.path.join(fastqc_folder,
        trimming_prefix + "_R2_trim_fastqc.html")
    if ngstk.check_command(tools.fastqc):
        ngstk.make_dir(fastqc_folder)

    pm.info("trimmomatic local_input_files: {}".format(local_input_files))
    if not param.java_settings.params:
        java_settings = '-Xmx{mem}'.format(mem=pm.mem)
    else:
        java_settings = param.java_settings.params
    trim_cmd_chunks = [
        "{java} {settings} -jar {trim} {PE} -threads {cores}".format(
            java=tools.java, settings=java_settings,
            trim=tools.trimmomatic,
            PE="PE" if args.paired_end else "SE",
            cores=pm.cores),
        untrimmed_fastq1,
        untrimmed_fastq2 if args.paired_end else None,
        trimmed_fastq,
        trimming_prefix + "_R1_unpaired.fq" if args.paired_end else None,
        trimmed_fastq_R2 if args.paired_end else None,
        trimming_prefix + "_R2_unpaired.fq" if args.paired_end else None,
        "ILLUMINACLIP:" + res.adapters + ":2:30:10"
    ]
    trim_cmd = build_command(trim_cmd_chunks)

    def check_trim():
        pm.info("Evaluating read trimming")
        
        if args.paired_end and not trimmed_fastq_R2:
            pm.warning("Specified paired-end but no R2 file")

        n_trim = int(ngstk.count_reads(trimmed_fastq, args.paired_end))
        pm.report_result("Trimmed_reads", int(n_trim))
        try:
            rr = int(pm.get_stat("Raw_reads"))
        except:
            pm.warning("Can't calculate trim loss rate without raw read result.")
        else:
            pm.report_result(
                "Trim_loss_rate", round((rr - n_trim) * 100 / rr, 2))

        # Also run a fastqc (if installed/requested)
        if fastqc_folder and os.path.isabs(fastqc_folder):
            ngstk.make_sure_path_exists(fastqc_folder)
        cmd = (tools.fastqc + " --noextract --outdir " +
               fastqc_folder + " " + trimmed_fastq)
        pm.run(cmd, fastqc_report, nofail=False)
        pm.report_object("FastQC report r1", fastqc_report)
        if args.paired_end and trimmed_fastq_R2:
            cmd = (tools.fastqc + " --noextract --outdir " +
                   fastqc_folder + " " + trimmed_fastq_R2)
            pm.run(cmd, fastqc_report_R2, nofail=False)
            pm.report_object("FastQC report r2", fastqc_report_R2)

    pm.run(trim_cmd, trimmed_fastq, follow=check_trim)

    pm.clean_add(os.path.join(fastq_folder, "*.fastq"), conditional=True)
    pm.clean_add(os.path.join(fastq_folder, "*.log"), conditional=True)

    # Prepare variables for alignment step
    if args.paired_end:
        unmap_fq1 = trimmed_fastq
        unmap_fq2 = trimmed_fastq_R2
    else:
        unmap_fq1 = trimmed_fastq
        unmap_fq2 = None
else:
    if args.paired_end:
        unmap_fq1 = untrimmed_fastq1
        unmap_fq2 = untrimmed_fastq2
    else:
        unmap_fq1 = untrimmed_fastq1
        unmap_fq2 = None

############################################################################
#                          Genome Alignment                                #
############################################################################

pm.timestamp("### Genome Alignment: ")

# Prepare alignment output folder
map_genome_folder = os.path.join(param.outfolder,
                                 "aligned_" + args.genome_assembly)
ngstk.make_dir(map_genome_folder)

# Primary endpoint file following alignment and deduplication
mapping_genome_bam_star_path = map_genome_folder + "/" + args.sample_name + "."
mapping_genome_bam_star = mapping_genome_bam_star_path + "Aligned.sortedByCoord.out.bam"
mapping_genome_bam_log = mapping_genome_bam_star_path + "Log.final.out"
mapping_genome_bam = mapping_genome_bam_star_path + "bam"
mapping_genome_bam_idx = mapping_genome_bam + ".bai"
mapping_genome_bam_dedup = mapping_genome_bam_star_path + "dedup.bam"
mapping_genome_bam_dedup_metrics = mapping_genome_bam_star_path + "dedup.metrics.txt"
mapping_genome_bam_dedup_unique = mapping_genome_bam_star_path + "dedup.unique.bam"
mapping_genome_bam_dedup_unique_idx = mapping_genome_bam_star_path + "dedup.unique.bam.bai"
mapping_genome_bam_dedup_unique_bw = mapping_genome_bam_star_path + "dedup.unique.bw"

#STAR alignment command
if args.protocol == "RNA":
    #annotated genomes for RNA-seq
    cmd = "STAR" + " --runThreadN " + str(pm.cores)
    cmd += " --quantMode TranscriptomeSAM GeneCounts --outSAMtype BAM SortedByCoordinate --runMode alignReads --outFilterMultimapNmax 5000 --outSAMmultNmax 1 --outFilterMismatchNmax 3 --outMultimapperOrder Random --winAnchorMultimapNmax 5000 --alignEndsType EndToEnd --seedSearchStartLmax 30 --alignTranscriptsPerReadNmax 30000 --alignWindowsPerReadNmax 30000 --alignTranscriptsPerWindowNmax 300 --seedPerReadNmax 3000 --seedPerWindowNmax 300 --seedNoneLociPerWindow 1000 --genomeDir /work/project/becgsc_001/genomes/mm10/STAR " 
    cmd += "--readFilesCommand zcat --readFilesIn " + unmap_fq1 
    if args.paired_end:
                cmd += " " + unmap_fq2 + " "
    cmd += "--outFileNamePrefix " + mapping_genome_bam_star_path
    cmd2 = "mv " + mapping_genome_bam_star + " " + mapping_genome_bam
else:
    #non-annotated genomes and for others
    cmd = "STAR" + " --runThreadN " + str(pm.cores)
    cmd += " --outSAMtype BAM SortedByCoordinate --runMode alignReads --outFilterMultimapNmax 5000 --outSAMmultNmax 1 --outFilterMismatchNmax 3 --outMultimapperOrder Random --winAnchorMultimapNmax 5000 --alignEndsType EndToEnd --alignIntronMax 1 --alignMatesGapMax 350 --seedSearchStartLmax 30 --alignTranscriptsPerReadNmax 30000 --alignWindowsPerReadNmax 30000 --alignTranscriptsPerWindowNmax 300 --seedPerReadNmax 3000 --seedPerWindowNmax 300 --seedNoneLociPerWindow 1000 --genomeDir /home/gschotta/refgenie.genomes/alias/mm10/star_index/default " 
    cmd += "--readFilesCommand zcat --readFilesIn " + unmap_fq1 
    if args.paired_end:
                cmd += " " + unmap_fq2 + " "
    cmd += "--outFileNamePrefix " + mapping_genome_bam_star_path
    cmd2 = "mv " + mapping_genome_bam_star + " " + mapping_genome_bam

def check_alignment_genome():
     x = subprocess.check_output("awk -F\| 'NR==6 { print $2 }' " + mapping_genome_bam_log, shell=True)
     ir = int(x.decode().strip())
     pm.report_result("Input_reads", ir)
     x = subprocess.check_output("awk -F\| 'NR==9 { print $2 }' " + mapping_genome_bam_log, shell=True)
     ur = int(x.decode().strip())
     pm.report_result("Unique_reads", ur)
     x = subprocess.check_output("awk -F\| 'NR==24 { print $2 }' " + mapping_genome_bam_log, shell=True)
     mmr = int(x.decode().strip())
     pm.report_result("Multimapped_reads", mmr)
     x = subprocess.check_output("awk -F\| 'NR==26 { print $2 }' " + mapping_genome_bam_log, shell=True)
     mmxr = int(x.decode().strip())
     pm.report_result("Multimapped_too_many_loci_reads", mmxr)
     x1 = subprocess.check_output("awk -F\| 'NR==29 { print $2 }' " + mapping_genome_bam_log, shell=True)
     x2 = subprocess.check_output("awk -F\| 'NR==31 { print $2 }' " + mapping_genome_bam_log, shell=True)
     x3 = subprocess.check_output("awk -F\| 'NR==33 { print $2 }' " + mapping_genome_bam_log, shell=True)
     unr = int(x1.decode().strip()) + int(x2.decode().strip()) + int(x3.decode().strip())
     pm.report_result("Mapped_reads", (ur+mmr+mmxr))
     pm.report_result("Unmapped_reads", unr)
     pm.report_result("Mapping_rate", round((ur+mmr+mmxr)*100/ir,2))
     pm.report_result("Unique_Mapping_rate", round((ur)*100/ir,2))
     pm.report_result("Multi_Mapping_rate", round((mmr+mmxr)*100/ir,2))
        
pm.run([cmd, cmd2], mapping_genome_bam, follow=check_alignment_genome)

#Index Bam file
cmd = tools.samtools + " index " + mapping_genome_bam
pm.run(cmd, mapping_genome_bam_idx)

#Report Mapping statistics
stats = mapping_genome_bam_star_path + "Log.final.out"

def check_duplicates():
    #Report duplication rate (line 8 in metric.txt file)
    #Read Pair Duplicates (column 7)
    x = subprocess.check_output("awk -F'\t' 'NR==8 {print $7}' " + mapping_genome_bam_dedup_metrics, shell=True)
    dup = int(x.decode().strip())
    #Read Pair Optical Duplicates (column 8)
    x = subprocess.check_output("awk -F'\t' 'NR==8 {print $8}' " + mapping_genome_bam_dedup_metrics, shell=True)
    optdup = int(x.decode().strip())
    #Percent Duplication (column 9)
    x = subprocess.check_output("awk -F'\t' 'NR==8 {print $9}' " + mapping_genome_bam_dedup_metrics, shell=True)
    percdup = float(x.decode().strip())
    #Estimated Library Size (column 10)
    x = subprocess.check_output("awk -F'\t' 'NR==8 {print $10}' " + mapping_genome_bam_dedup_metrics, shell=True)
    libsize = int(x.decode().strip())
    pm.report_result("Read_pair_duplicates", dup)
    pm.report_result("Read_pair_optical_duplicates", optdup)
    pm.report_result("Percent_duplication", percdup)
    pm.report_result("Estimated_library_size", libsize)

#Remove duplicates
cmd = "java -jar " + tools.picard + " MarkDuplicates -I " + mapping_genome_bam
cmd += " -O " + mapping_genome_bam_dedup + " -M " + mapping_genome_bam_dedup_metrics
pm.run(cmd, mapping_genome_bam_dedup, follow=check_duplicates)

#Remove multimapping reads based on MAPQ=255
cmd = tools.samtools + " view -b -q 255 " + mapping_genome_bam_dedup + " > " + mapping_genome_bam_dedup_unique
pm.run(cmd, mapping_genome_bam_dedup_unique)

#Index deduplicated and unique BAM file
cmd = tools.samtools + " index " + mapping_genome_bam_dedup_unique
pm.run(cmd, mapping_genome_bam_dedup_unique_idx)

#Generate BigWig files using Deeptools
cmd = tools.bamcoverage + " --bam " + mapping_genome_bam_dedup_unique
cmd += " -o " + mapping_genome_bam_dedup_unique_bw + " --binSize 10 --normalizeUsing RPKM"
pm.run(cmd, mapping_genome_bam_dedup_unique_bw)

#Clean Temporary Files
pm.clean_add(mapping_genome_bam_dedup)

############################################################################
#                          IAP Coverage                                    #
############################################################################

pm.timestamp("### IAP Coverage: ")

# Prepare output folder
IAP_coverage_folder = os.path.join(param.outfolder,
                                 "IAP_coverage")
ngstk.make_dir(IAP_coverage_folder)

#processing files
IAP_plus = IAP_coverage_folder + "/" + args.sample_name + ".IAP.plus.txt"
IAP_minus = IAP_coverage_folder + "/" + args.sample_name + ".IAP.minus.txt"
IAP_norm_coverage = IAP_coverage_folder + "/" + args.sample_name + ".IAP.norm.coverage.txt"

#generate Coverage using bedtools coverage
#Plus orientation
cmd = tools.bedtools + " coverage -d -a " + res.gag_plus
cmd += " -b " + mapping_genome_bam
cmd += " > " + IAP_plus
pm.run(cmd, IAP_plus)

#Minus Orientation
cmd = tools.bedtools + " coverage -d -a " + res.gag_minus
cmd += " -b " + mapping_genome_bam
cmd += " > " + IAP_minus
pm.run(cmd, IAP_minus)

#summarize and normalize coverage
norm_factor = float(pm.get_stat("Mapped_reads"))/1000000
cmd = "tac " + IAP_minus + " | cat " + IAP_plus
cmd += " | awk -vN=15413 '{s[(NR-1)%N]+=$5}END{for(i=0;i<N;i++){print s[i]/" + str(norm_factor) + "}}'"
cmd += " > " + IAP_norm_coverage

pm.run(cmd, IAP_norm_coverage, shell=True)

pm.clean_add(IAP_plus)
pm.clean_add(IAP_minus)

############################################################################
#                          Feature Counts                                  #
############################################################################

pm.timestamp("### Feature Counts: ")

# Prepare output folder
feature_counts_folder = os.path.join(param.outfolder,
                                 "feature_counts")
ngstk.make_dir(feature_counts_folder)

#Target Files
feature_counts_temp = feature_counts_folder + "/" + args.sample_name + ".fc.tmp.txt"
feature_counts_result = feature_counts_folder + "/" + args.sample_name + ".fc.txt"

#perform featurecounts (subread Package)
#TODO install subread package in conda environment
cmd = tools.featureCounts + " -M -F SAF -T 1 -s 0 -p -a " + res.repeats_SAF
cmd += " -o " + feature_counts_temp + " " + mapping_genome_bam

pm.run(cmd, feature_counts_temp)

#reformat output file
cmd = "awk '{print $1,$6,$7}' " + feature_counts_temp + " > " + feature_counts_result
pm.run(cmd, feature_counts_result)

pm.clean_add(feature_counts_temp)

############################################################################
#                          Pipeline finished                               #
############################################################################

pm.stop_pipeline()