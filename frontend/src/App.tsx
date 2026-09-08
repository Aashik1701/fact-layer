import React, { useState } from 'react';
import { AppShell } from '@/components/layout/AppShell';
import { NavTab } from '@/components/layout/Sidebar';
import { OverviewPage } from '@/components/overview/OverviewPage';
import { RelationsPage } from '@/components/relations/RelationsPage';
import { FactsPage } from '@/components/facts/FactsPage';
import { DocumentsPage } from '@/components/documents/DocumentsPage';
import { RequiredCasesPage } from '@/components/cases/RequiredCasesPage';
import { RejectedFactsPage } from '@/components/rejected/RejectedFactsPage';
import { ClustersPage } from '@/components/clusters/ClustersPage';
import { ClusterInfo } from '@/types';

interface FactsNavState {
  docId?: string | null;
  subject?: string | null;
  measure?: string | null;
}

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<NavTab>('overview');
  // Last cross-page navigation intent into the Facts Explorer. Changing it
  // remounts FactsPage with fresh initial filters so a doc/cluster pick never
  // leaks stale state or gets dropped (the bug this fixes).
  const [factsNav, setFactsNav] = useState<FactsNavState>({});
  // Nonce forces a remount whenever we navigate into facts with a filter,
  // so the freshly navigated FactsPage reads the latest initial filters.
  const [factsNavKey, setFactsNavKey] = useState(0);

  const goToFacts = (state: FactsNavState) => {
    setFactsNav(state);
    setFactsNavKey((k) => k + 1);
    setCurrentTab('facts');
  };

  const goToCluster = (cluster: ClusterInfo) => {
    const sepIdx = cluster.cluster_key.indexOf('::');
    const subject = sepIdx >= 0 ? cluster.cluster_key.slice(0, sepIdx) : cluster.cluster_key;
    const measure = sepIdx >= 0 ? cluster.cluster_key.slice(sepIdx + 2) : '';
    goToFacts({ subject, measure });
  };

  // Seeded relation-type filter (from a TopBar chip) + nonce to remount so
  // RelationsPage picks up the freshly selected type instead of keeping old.
  const [relationsType, setRelationsType] = useState<string | null>(null);
  const [relationsKey, setRelationsKey] = useState(0);
  const startRelationType = (type: string) => {
    setRelationsType(type);
    setRelationsKey((k) => k + 1);
    setCurrentTab('relations');
  };

  const renderContent = () => {
    switch (currentTab) {
      case 'overview':
        return (
          <OverviewPage
            onNavigateToCases={() => setCurrentTab('cases')}
            onNavigateToRelations={() => setCurrentTab('relations')}
            onNavigateToFacts={() => setCurrentTab('facts')}
          />
        );
      case 'relations':
        return <RelationsPage key={relationsKey} initialType={relationsType} />;
      case 'facts':
        return (
          <FactsPage
            key={factsNavKey}
            initialDocId={factsNav.docId}
            initialSubject={factsNav.subject}
            initialMeasure={factsNav.measure}
          />
        );
      case 'documents':
        return (
          <DocumentsPage
            onNavigateToFacts={(docId) => goToFacts({ docId })}
          />
        );
      case 'cases':
        return (
          <RequiredCasesPage
            onNavigateToIngest={() => setCurrentTab('documents')}
          />
        );
      case 'rejected':
        return <RejectedFactsPage />;
      case 'clusters':
        return (
          <ClustersPage
            onSelectCluster={goToCluster}
          />
        );
      default:
        return (
          <OverviewPage
            onNavigateToCases={() => setCurrentTab('cases')}
            onNavigateToRelations={() => setCurrentTab('relations')}
            onNavigateToFacts={() => setCurrentTab('facts')}
          />
        );
    }
  };

  return (
    <AppShell
      currentTab={currentTab}
      onTabChange={(tab) => setCurrentTab(tab)}
      onSelectRelationType={startRelationType}
    >
      {renderContent()}
    </AppShell>
  );
};

export default App;
