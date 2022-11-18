import {createRef} from 'react';
import {Observer} from 'mobx-react';

import EmptyStateWarning from 'sentry/components/emptyStateWarning';
import {t} from 'sentry/locale';
import {Organization} from 'sentry/types';
import {CustomerProfiler} from 'sentry/utils/performanceForSentry';

import * as CursorGuideHandler from './cursorGuideHandler';
import * as DividerHandlerManager from './dividerHandlerManager';
import DragManager, {DragManagerChildrenProps} from './dragManager';
import TraceViewHeader from './header';
import * as ScrollbarManager from './scrollbarManager';
import * as SpanContext from './spanContext';
import SpanTree from './spanTree';
import {getTraceContext} from './utils';
import WaterfallModel from './waterfallModel';

type Props = {
  organization: Organization;
  waterfallModel: WaterfallModel;
  isEmbedded?: boolean;
};

function TraceView(props: Props) {
  const traceViewRef = createRef<HTMLDivElement>();
  const virtualScrollBarContainerRef = createRef<HTMLDivElement>();
  const minimapInteractiveRef = createRef<HTMLDivElement>();

  const renderHeader = (dragProps: DragManagerChildrenProps) => (
    <Observer>
      {() => {
        const {waterfallModel} = props;

        return (
          <TraceViewHeader
            organization={props.organization}
            minimapInteractiveRef={minimapInteractiveRef}
            dragProps={dragProps}
            trace={waterfallModel.parsedTrace}
            event={waterfallModel.event}
            virtualScrollBarContainerRef={virtualScrollBarContainerRef}
            operationNameFilters={waterfallModel.operationNameFilters}
            rootSpan={waterfallModel.rootSpan.span}
            spans={waterfallModel.getWaterfall({
              viewStart: 0,
              viewEnd: 1,
            })}
            generateBounds={waterfallModel.generateBounds({
              viewStart: 0,
              viewEnd: 1,
            })}
          />
        );
      }}
    </Observer>
  );

  const {organization, waterfallModel, isEmbedded} = props;

  if (!getTraceContext(waterfallModel.event)) {
    return (
      <EmptyStateWarning>
        <p>{t('There is no trace for this transaction')}</p>
      </EmptyStateWarning>
    );
  }

  return (
    <SpanContext.Provider>
      <SpanContext.Consumer>
        {spanContextProps => (
          <DragManager interactiveLayerRef={minimapInteractiveRef}>
            {(dragProps: DragManagerChildrenProps) => (
              <Observer>
                {() => {
                  const parsedTrace = waterfallModel.parsedTrace;
                  return (
                    <CursorGuideHandler.Provider
                      interactiveLayerRef={minimapInteractiveRef}
                      dragProps={dragProps}
                      trace={parsedTrace}
                    >
                      <DividerHandlerManager.Provider interactiveLayerRef={traceViewRef}>
                        <DividerHandlerManager.Consumer>
                          {dividerHandlerChildrenProps => {
                            return (
                              <ScrollbarManager.Provider
                                dividerPosition={
                                  dividerHandlerChildrenProps.dividerPosition
                                }
                                interactiveLayerRef={virtualScrollBarContainerRef}
                                dragProps={dragProps}
                                isEmbedded={isEmbedded}
                              >
                                {renderHeader(dragProps)}
                                <Observer>
                                  {() => (
                                    <CustomerProfiler id="SpanTree">
                                      <SpanTree
                                        traceViewRef={traceViewRef}
                                        dragProps={dragProps}
                                        organization={organization}
                                        waterfallModel={waterfallModel}
                                        filterSpans={waterfallModel.filterSpans}
                                        spans={waterfallModel.getWaterfall({
                                          viewStart: dragProps.viewWindowStart,
                                          viewEnd: dragProps.viewWindowEnd,
                                        })}
                                        focusedSpanIds={waterfallModel.focusedSpanIds}
                                        spanContextProps={spanContextProps}
                                      />
                                    </CustomerProfiler>
                                  )}
                                </Observer>
                              </ScrollbarManager.Provider>
                            );
                          }}
                        </DividerHandlerManager.Consumer>
                      </DividerHandlerManager.Provider>
                    </CursorGuideHandler.Provider>
                  );
                }}
              </Observer>
            )}
          </DragManager>
        )}
      </SpanContext.Consumer>
    </SpanContext.Provider>
  );
}

export default TraceView;
