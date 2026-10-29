import { Tabs, type BottomTabBarProps } from "expo-router/js-tabs";
import type { SFSymbol } from "expo-symbols";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Floating, Glyph, tabBarBottom } from "@/components/ui";
import { color } from "@/lib/theme";

/** The three tabs, their words and their SF Symbols (outline, and filled once chosen where SF Symbols has one). */
const TABS: Record<string, { title: string; icon: SFSymbol; on: SFSymbol }> = {
  index: { title: "Home", icon: "house", on: "house.fill" },
  listings: { title: "My listings", icon: "list.bullet", on: "list.bullet" },
  search: { title: "My search", icon: "slider.horizontal.3", on: "slider.horizontal.3" },
};

/** Signed in: Home, My listings and My search, under one glass tab bar floating over the bottom of the screen. */
export default function TabsLayout() {
  return (
    <Tabs
      tabBar={(props) => <TabBar {...props} />}
      screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: color.ground } }}
    >
      {Object.entries(TABS).map(([name, t]) => <Tabs.Screen key={name} name={name} options={{ title: t.title }} />)}
    </Tabs>
  );
}

/** Lucent's tab bar: a glass pill of three 96 × 54 tabs, the chosen one in ink on a tint. */
function TabBar({ state, emitter, navigateToTab }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  return (
    <View pointerEvents="box-none" style={[styles.wrap, { bottom: tabBarBottom(insets.bottom) }]}>
      <Floating style={styles.bar}>
        <View accessibilityRole="tablist" style={styles.row}>
          {state.routes.map((route, index) => {
            const t = TABS[route.name];
            if (!t) return null;
            const focused = index === state.index;
            const press = () => {
              const event = emitter.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
              if (!focused && !event.defaultPrevented) navigateToTab(route.key);
            };
            return (
              <Pressable
                key={route.key}
                accessibilityRole="tab"
                accessibilityLabel={t.title}
                accessibilityState={{ selected: focused }}
                onPress={press}
                style={({ pressed }) => [styles.tab, focused && styles.tabOn, pressed && { transform: [{ scale: 0.96 }] }]}
              >
                <Glyph name={focused ? t.on : t.icon} size={22} tint={focused ? color.ink : color.ink2} />
                <Text style={[styles.label, focused && { color: color.ink }]} numberOfLines={1}>{t.title}</Text>
              </Pressable>
            );
          })}
        </View>
      </Floating>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", left: 0, right: 0, alignItems: "center" },
  bar: { padding: 6 },
  row: { flexDirection: "row", gap: 4 },
  tab: { width: 96, height: 54, borderRadius: 999, alignItems: "center", justifyContent: "center", gap: 3 },
  tabOn: { backgroundColor: color.tint },
  label: { fontSize: 13, lineHeight: 15, fontWeight: "600", color: color.ink2 },
});
