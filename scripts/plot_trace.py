"""Plot fixed-window old-worker CPU service and priority from validated traces."""
import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from replay_trace import read_events, CHARGE, BOOST, CONFIG
from xv6 import ROOT


def main():
    directory=ROOT/'results/v2/pressure'
    with (directory/'summary.csv').open() as stream:
        rows=list(csv.DictReader(stream))
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True,layout='constrained')
    for row,color in zip(rows,['#607d8b','#1565c0']):
        start,end,pid=(int(row[k]) for k in ['start','end','old_pid'])
        boost=int(row['boost'])
        events=read_events(directory/f'boost-{boost}.log')
        xs,ys=[0],[0]
        service=0
        px,py=[0],[3]
        for e in events:
            if e['type']==CHARGE and e['pid']==pid:
                lo=max(start,e['time']-e['amount']); hi=min(end,e['time'])
                if hi>lo:
                    xs.extend([(lo-start)/10000,(hi-start)/10000])
                    ys.extend([service/10000,(service+hi-lo)/10000])
                    service+=hi-lo
            if start<=e['time']<=end:
                if e['type']==BOOST or (e['type']==CONFIG and e['detail'] & 2):
                    px.append((e['time']-start)/10000); py.append(0)
                elif e['pid']==pid:
                    px.append((e['time']-start)/10000); py.append(e['level'])
        xs.append((end-start)/10000); ys.append(service/10000)
        px.append((end-start)/10000); py.append(py[-1])
        label=f'boost={boost}'
        axes[0].plot(xs,ys,label=label,color=color)
        axes[1].step(px,py,where='post',label=label,color=color)
    axes[0].set_ylabel('Old-worker CPU service (ms)')
    axes[0].legend()
    axes[0].set_title('Identical offered burst; exact execution intervals clipped to a fixed window')
    axes[1].set_yticks(range(4),['Q0','Q1','Q2','Q3'])
    axes[1].invert_yaxis()
    axes[1].set_ylabel('Old-worker priority')
    axes[1].set_xlabel('Time since burst release (ms)')
    for ax in axes:
        ax.spines[['top','right']].set_visible(False)
        ax.grid(alpha=.2)
    fig.savefig(directory/'priority_and_service.png',dpi=180)
    fig.savefig(directory/'priority_and_service.pdf')


if __name__=='__main__':
    main()
