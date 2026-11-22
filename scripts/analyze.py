"""Summarize independent multicore runs and render reproducible figures."""
import argparse
from collections import defaultdict
import csv
from pathlib import Path
from statistics import median

TIMEBASE=10000000


def summarize(directory):
    with (directory/'samples.csv').open() as stream:
        rows=list(csv.DictReader(stream))
    groups=defaultdict(list)
    for r in rows:
        groups[int(r['cpus']),r['mode'],r['policy'],int(r['run'])].append(r)
    samples=[]
    for (cpus,mode,policy,run),records in sorted(groups.items()):
        n=len(records)
        value=lambda key: [int(r[key]) for r in records]
        release=min(value('release'))
        elapsed=max(value('done'))-release
        total_cpu=sum(value('cpu_time'))
        special=max(records,key=lambda r:int(r['worker']))
        data={
            'mean_turnaround_ms':sum(int(r['done'])-int(r['release']) for r in records)/n/10000,
            'mean_initial_response_ms':sum(int(r['first'])-int(r['release']) for r in records)/n/10000,
            'mean_wait_ms':sum(value('wait_time'))/n/10000,
            'makespan_ms':elapsed/10000,
            'jobs_per_second':n*TIMEBASE/elapsed,
            'work_cpu_utilization':total_cpu/(cpus*elapsed),
            'dispatch_spans':n+sum(value('dispatches')),
            'migrations':sum(value('migrations')),
            'special_turnaround_ms':(int(special['done'])-int(special['release']))/10000,
        }
        if int(special['wakeups']):
            data['special_wake_delay_ms']=int(special['wake_delay'])/int(special['wakeups'])/10000
        if int(special['pipe_samples']):
            data['pipe_message_delay_ms']=int(special['pipe_delay'])/int(special['pipe_samples'])/10000
            data['pipe_message_max_ms']=int(special['pipe_max'])/10000
        if mode=='fair':
            service=value('cpu_time')
            # The common deadline is checked between work chunks. Retain
            # checkpoint overshoot rather than claiming exact clipping.
            deadline=release+int(records[0]['units'])*10000
            data['fair_stop_overshoot_ms']=max(0,max(value('done'))-deadline)/10000
            data['jain_fairness']=sum(service)**2/(n*sum(v*v for v in service))
            data['work_units_per_second']=sum(value('work_units'))*TIMEBASE/elapsed
        for metric,v in data.items():
            samples.append(dict(cpus=cpus,mode=mode,policy=policy,run=run,metric=metric,value=v))
    with (directory/'run_metrics.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(samples[0]))
        writer.writeheader(); writer.writerows(samples)
    grouped=defaultdict(list)
    for r in samples:
        grouped[r['cpus'],r['mode'],r['policy'],r['metric']].append(r['value'])
    summaries=[]
    for (cpus,mode,policy,metric),values in sorted(grouped.items()):
        summaries.append(dict(cpus=cpus,mode=mode,policy=policy,metric=metric,runs=len(values),
                              median=median(values),minimum=min(values),maximum=max(values)))
    with (directory/'summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(summaries[0]))
        writer.writeheader(); writer.writerows(summaries)
    return rows,groups,grouped


def plots(directory, grouped):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    figures=directory/'figures'; figures.mkdir(exist_ok=True)
    cpus=sorted({key[0] for key in grouped})
    modes={key[1] for key in grouped}
    styles=[('DEFAULT','#607d8b','o'),('MLFQ','#1565c0','s')]

    def lines(ax,mode,metric,label,log=False):
        for policy,color,marker in styles:
            points=[c for c in cpus if (c,mode,policy,metric) in grouped]
            if not points: continue
            values=[grouped[c,mode,policy,metric] for c in points]
            centers=[median(v) for v in values]
            error=[[c-min(v) for c,v in zip(centers,values)],
                   [max(v)-c for c,v in zip(centers,values)]]
            ax.errorbar(points,centers,yerr=error,marker=marker,color=color,label=policy,capsize=3)
        ax.set_xticks(cpus)
        ax.set_xlabel('Emulated CPUs')
        ax.set_ylabel(label)
        if log: ax.set_yscale('log')
        else: ax.set_ylim(bottom=0)
        ax.grid(alpha=.2)

    def save(fig,name):
        fig.savefig(figures/(name+'.png'),dpi=180)
        fig.savefig(figures/(name+'.pdf'))
        plt.close(fig)

    latency_modes=[mode for mode in ['mixed','pipe'] if mode in modes]
    if latency_modes:
        fig,axes=plt.subplots(1,len(latency_modes),figsize=(5.5*len(latency_modes),4.4),
                              squeeze=False,layout='constrained')
        for ax,mode in zip(axes.flat,latency_modes):
            lines(ax,mode,'special_wake_delay_ms','Mean wakeup delay (ms; log scale)',True)
            ax.set_title(mode)
        axes.flat[0].legend()
        fig.suptitle('Median across independent runs; error bars are observed min-max')
        save(fig,'wakeup_latency')

    turnaround_modes=[m for m in ['cpu','mixed','short','pipe','phase','fair'] if m in modes]
    columns=min(3,len(turnaround_modes))
    rows=(len(turnaround_modes)+columns-1)//columns
    fig,axes=plt.subplots(rows,columns,figsize=(4.4*columns,3.5*rows),
                          squeeze=False,layout='constrained')
    for ax,mode in zip(axes.flat,turnaround_modes):
        if mode=='fair':
            lines(ax,mode,'jain_fairness',"Jain's CPU-service fairness")
            ax.set_ylim(0,1.02)
            ax.set_title('Fixed-window fairness')
        else:
            lines(ax,mode,'mean_turnaround_ms','Mean turnaround (ms)')
            ax.set_title(mode)
    for ax in list(axes.flat)[len(turnaround_modes):]:
        ax.set_visible(False)
    axes.flat[0].legend()
    fig.suptitle('Matched workloads at each CPU count; median and observed range')
    save(fig,'turnaround_and_fairness')

    if 'cpu' in modes:
        fig,axes=plt.subplots(1,2,figsize=(11,4.4),layout='constrained')
        lines(axes[0],'cpu','dispatch_spans','Worker dispatch spans')
        lines(axes[1],'cpu','makespan_ms','CPU-workload makespan (ms)')
        axes[0].legend()
        fig.suptitle('CPU workload: dispatch spans include the initial work turn')
        save(fig,'cpu_execution')

    if 'short' in modes:
        fig,ax=plt.subplots(figsize=(7,4.5),layout='constrained')
        lines(ax,'short','special_turnaround_ms','Short-job turnaround (ms)')
        ax.legend()
        ax.set_title('Short-job behavior is reported separately from the workload mean')
        save(fig,'short_job')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    rows,runs,grouped=summarize(args.directory)
    plots(args.directory,grouped)
    print(f'Summarized {len(rows)} worker records from {len(runs)} independent boots.')


if __name__=='__main__':
    main()
